"""Exchange build tasks with a manually configured SSH peer."""
import argparse
import base64
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import openbuildnet
from worker_events import LinuxResourceEvents

MAX_REQUEST = 1024 * 1024
MAX_LOG = 1024 * 1024
MIB = 1024 * 1024
MONITOR_DEFAULTS = {"active_seconds": 10, "pressure_seconds": 2, "full_seconds": 900,
                    "event_seconds": 1, "recovery_seconds": 30, "recovery_margin_bytes": 64 * MIB}
DEFAULT_RESERVES = {"memory": 256 * MIB, "persistent_disk": 1024 * MIB,
                    "snapshot_disk": 64 * MIB}
RECEIVE_COMMAND = (
    'if [ -x "$HOME/.local/bin/openbuildnet-peer" ]; then '
    'exec "$HOME/.local/bin/openbuildnet-peer" receive; '
    'else exec openbuildnet-peer receive; fi'
)


def _measurement(scope, source, reader):
    """Read available bytes and report an explicit unknown on probe failure."""
    try:
        available = reader()
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        return {"scope": scope, "source": source, "measurement_status": "unknown",
                "measured_at": None, "available_bytes": None}
    return {"scope": scope, "source": source, "measurement_status": "ok",
            "measured_at": datetime.now(timezone.utc).isoformat(), "available_bytes": available}


def _disk_measurement(path):
    """Return disk availability and the device ID used to identify shared capacity."""
    reading = _measurement(None, str(path), lambda: shutil.disk_usage(path).free)
    try:
        reading["scope"] = f"filesystem:{path.stat().st_dev}"
    except OSError:
        pass
    return reading


def _available_memory():
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise ValueError("MemAvailable is missing")


def _measure_resources():
    """Measure Linux host and local Docker resources for read-only diagnostics."""
    if sys.platform != "linux":
        return None
    resources = {
        "memory": _measurement("linux-host", "/proc/meminfo:MemAvailable", _available_memory),
        "state_disk": _disk_measurement(openbuildnet.STATE),
        "snapshot_disk": _disk_measurement(Path(tempfile.gettempdir())),
    }
    try:
        endpoint = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
        if not endpoint:
            endpoint = openbuildnet.docker(
                "context", "inspect", "--format", "{{.Endpoints.docker.Host}}",
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        if endpoint not in ("unix:///var/run/docker.sock", "unix:///run/docker.sock"):
            raise ValueError("expected the local system Docker socket")
        info = json.loads(openbuildnet.docker(
            "info", "--format", "{{json .}}", capture_output=True, text=True, timeout=5,
        ).stdout)
        root = Path(info["DockerRootDir"])
        if not root.is_absolute():
            raise ValueError("missing Docker storage directory")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        resources["runtime_disk"] = {
            "scope": None, "source": "DockerRootDir", "measurement_status": "unknown",
            "measured_at": None, "available_bytes": None,
        }
        return resources

    resources["runtime_disk"] = _disk_measurement(root)
    if ["driver-type", "io.containerd.snapshotter.v1"] in (info.get("DriverStatus") or []):
        containerd_root = Path(os.environ.get("OPENBUILDNET_CONTAINERD_ROOT", "/var/lib/containerd"))
        resources["containerd_disk"] = _disk_measurement(containerd_root)
    return resources


def _system_reserves():
    """Read nonnegative byte overrides from the receiving worker's environment."""
    reserves = {}
    for resource, default in DEFAULT_RESERVES.items():
        value = int(os.environ.get(f"OPENBUILDNET_{resource.upper()}_RESERVE_BYTES", default))
        if value < 0:
            raise ValueError(f"{resource} reserve cannot be negative")
        reserves[resource] = value
    return reserves


def _monitor_settings():
    """Read finite nonnegative settings, requiring positive polling intervals."""
    settings = {}
    for name, default in MONITOR_DEFAULTS.items():
        raw = os.environ.get(f"OPENBUILDNET_MONITOR_{name.upper()}", default)
        value = int(raw) if name.endswith("_bytes") else float(raw)
        if not 0 <= value < float("inf"):
            raise ValueError(f"invalid monitor setting: {name}")
        if name.endswith("_seconds") and name != "recovery_seconds" and value == 0:
            raise ValueError(f"monitor interval must be positive: {name}")
        settings[name] = value
    settings["events_enabled"] = os.environ.get("OPENBUILDNET_MONITOR_EVENTS", "1") != "0"
    return settings


def _refresh_resources(previous):
    """Refresh known Linux paths without repeatedly querying the Docker daemon."""
    readings = {"memory": _measurement("linux-host", "/proc/meminfo:MemAvailable", _available_memory)}
    for resource, reading in previous.items():
        if resource != "memory":
            readings[resource] = _disk_measurement(Path(reading["source"]))
    return readings


def _resource_rejection(resources, reserves, *, include_task=True):
    """Check reserves and optionally one new fixed-profile build against readings."""
    demand_factor = int(include_task)
    pools = {}
    for resource, reading in resources.items():
        scope = reading["scope"]
        if reading["measurement_status"] != "ok" or scope is None:
            return {"accepted": False, "reason": "resource_unavailable", "resource": resource}
        if resource == "memory":
            if reading["available_bytes"] < reserves["memory"] + 128 * MIB * demand_factor:
                return {"accepted": False, "reason": "insufficient_memory", "resource": resource}
            continue
        pool = pools.setdefault(scope, {"available": reading["available_bytes"], "reserve": 0,
                                        "demands": {}, "resources": []})
        pool["available"] = min(pool["available"], reading["available_bytes"])
        pool["resources"].append(resource)
        if resource == "snapshot_disk":
            policy, demand, purpose = "snapshot_disk", 16 * MIB, "snapshot"
        elif resource == "state_disk":
            policy, demand, purpose = "persistent_disk", 16 * MIB, "state"
        else:
            # Docker and containerd may report the same backing store.
            policy, demand, purpose = "persistent_disk", 128 * MIB, "runtime"
        pool["reserve"] = max(pool["reserve"], reserves[policy])
        pool["demands"][purpose] = demand * demand_factor

    for scope, pool in pools.items():
        if pool["available"] < pool["reserve"] + sum(pool["demands"].values()):
            return {"accepted": False, "reason": "insufficient_disk",
                    "scope": scope, "resources": pool["resources"]}
    return None


def _write_json(path, value):
    """Publish a complete JSON snapshot so concurrent readers see a whole document."""
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(value), encoding="utf-8")
    for attempt in range(5):
        try:
            pending.replace(path)
            return
        except PermissionError:
            # Windows readers can briefly prevent replacement of an open snapshot.
            if attempt == 4:
                raise
            time.sleep(0.01)


def _resource_health(resources, reserves, *, warning_margin_bytes=0):
    """Describe current system headroom independently of the next task's demand."""
    rejection = _resource_rejection(resources, reserves, include_task=False)
    health = {"measured_at": datetime.now(timezone.utc).isoformat(), "status": "healthy"}
    if rejection:
        health.update({key: value for key, value in rejection.items() if key != "accepted"})
        health["status"] = "pressure"
        if rejection["reason"] == "resource_unavailable":
            health["status"] = "unknown"
    elif warning_margin_bytes:
        warning_reserves = {key: value + warning_margin_bytes for key, value in reserves.items()}
        warning = _resource_rejection(resources, warning_reserves, include_task=False)
        if warning:
            health.update({key: value for key, value in warning.items() if key != "accepted"})
            health.update(status="warning", reason="approaching_reserve")
    return health


def _admission_gate(resources, reserves, settings):
    """Persist pause and recovery decisions across SSH requests and the monitor."""
    pressure = _resource_rejection(resources, reserves, include_task=False)
    recovery_reserves = {
        key: value + settings["recovery_margin_bytes"] for key, value in reserves.items()
    }
    enough_to_recover = _resource_rejection(resources, recovery_reserves, include_task=False) is None
    now = time.time()
    with closing(sqlite3.connect(openbuildnet.STATE / "peer-health.sqlite3", timeout=2)) as database:
        database.execute("CREATE TABLE IF NOT EXISTS health (id INTEGER PRIMARY KEY, payload TEXT)")
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("SELECT payload FROM health WHERE id = 1").fetchone()
        gate = json.loads(row[0]) if row else {"paused": False, "recovery_since": None}
        if pressure:
            gate.update(paused=True, recovery_since=None, reason=pressure["reason"])
        elif gate["paused"]:
            gate["reason"] = "recovering_resources"
            if not enough_to_recover:
                gate["recovery_since"] = None
            else:
                if gate["recovery_since"] is None or gate["recovery_since"] > now:
                    gate["recovery_since"] = now
                if now - gate["recovery_since"] >= settings["recovery_seconds"]:
                    gate.update(paused=False, recovery_since=None, reason=None)
        else:
            gate["reason"] = None
        gate["checked_at"] = datetime.now(timezone.utc).isoformat()
        database.execute("INSERT OR REPLACE INTO health VALUES (1, ?)", (json.dumps(gate),))
        database.commit()
    return gate


def _watch_task_memory(events, task_id):
    """Locate a running local Docker container's cgroup and subscribe to memory.events."""
    if events.memory_path is not None:
        return
    try:
        inspection = openbuildnet.docker(
            "inspect", "--format", "{{.State.Pid}}", f"openbuildnet-{task_id}",
            capture_output=True, text=True, timeout=2,
        )
        pid = int(inspection.stdout)
        if pid <= 0:
            return
        for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines():
            if line.startswith("0::"):
                relative = line[3:].lstrip("/")
                path = (Path("/sys/fs/cgroup") / relative / "memory.events").resolve()
                if not path.is_relative_to(Path("/sys/fs/cgroup")):
                    raise ValueError("unexpected cgroup path")
                events.watch_memory(path)
                return
        events.errors["memory_events"] = "cgroup v2 path unavailable"
    except (FileNotFoundError, subprocess.CalledProcessError):
        # The monitor starts before creation and ends after container cleanup.
        pass
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        events.errors["memory_events"] = str(error)


@contextmanager
def _monitor_resources(active_path, active, resources, reserves, *, idle=False):
    """Sample Linux headroom during execution and yield a report for persistence.

    Args:
        active_path: Atomic snapshot read by concurrent diagnostic requests.
        active: Task identity and start time.
        resources: Initial admission readings, or None on unsupported platforms.
        reserves: Receiving node's configured system reserves.

    Yields:
        Latest reading and deduplicated health transitions, or None if unsupported.
    """
    if resources is None:
        yield None
        return

    settings = _monitor_settings()
    report = {"settings": settings, "events": []}
    stopped = threading.Event()
    wake_source = []

    def record(readings, subscriptions, trigger):
        health = _resource_health(
            readings, reserves, warning_margin_bytes=settings["recovery_margin_bytes"]
        )
        identity = {key: value for key, value in health.items() if key != "measured_at"}
        previous = report["events"][-1] if report["events"] else {}
        if identity != {key: value for key, value in previous.items() if key != "measured_at"}:
            report["events"].append(health)
            if len(report["events"]) > 128:
                report["events"].pop(0)
                report["events_dropped"] = report.get("events_dropped", 0) + 1
        report["latest"] = health
        report["admission_gate"] = _admission_gate(readings, reserves, settings)
        report["mode"] = "polling_fallback" if subscriptions.errors else "events_with_polling"
        report["subscription_errors"] = dict(subscriptions.errors)
        report["event_counts"] = dict(subscriptions.counts)
        report["event_sources"] = [
            name for name in subscriptions.sources.values() if name != "wakeup"
        ]
        report["last_trigger"] = trigger
        _write_json(active_path, {**active, "resource_monitor": report})

    def sample():
        subscriptions = None
        try:
            subscriptions = LinuxResourceEvents(settings["events_enabled"])
            wake_source[:] = [subscriptions]
            readings = resources
            report["last_subscription_check"] = datetime.now(timezone.utc).isoformat()
            record(readings, subscriptions, "startup")
            now = time.monotonic()
            last_sample, last_full, last_attach = now, now, now - 1
            pending_events = False
            while not stopped.is_set():
                now = time.monotonic()
                if settings["events_enabled"] and active.get("task_id") and now - last_attach >= 1:
                    _watch_task_memory(subscriptions, active["task_id"])
                    last_attach = now
                full_due = now - last_full >= settings["full_seconds"]
                pressured = report["latest"]["status"] != "healthy" or report["admission_gate"]["paused"]
                interval = settings["active_seconds"]
                if idle and not subscriptions.errors:
                    interval = settings["full_seconds"]
                if pressured:
                    interval = settings["pressure_seconds"]
                event_due = pending_events and now - last_sample >= settings["event_seconds"]
                if full_due or event_due or now - last_sample >= interval:
                    if full_due:
                        counts = dict(subscriptions.counts)
                        memory_path = subscriptions.memory_path
                        memory_counts = dict(subscriptions.memory_counts)
                        subscriptions.close()
                        subscriptions = LinuxResourceEvents(settings["events_enabled"])
                        wake_source[:] = [subscriptions]
                        subscriptions.counts.update(counts)
                        if memory_path is not None and memory_path.is_file():
                            subscriptions.watch_memory(memory_path, memory_counts)
                        readings = _measure_resources()
                        report["last_subscription_check"] = datetime.now(timezone.utc).isoformat()
                        last_full = time.monotonic()
                        trigger = "full_check"
                    else:
                        readings = _refresh_resources(readings)
                        trigger = "event" if event_due else "capacity_check"
                    record(readings, subscriptions, trigger)
                    last_sample = time.monotonic()
                    pending_events = False
                    if report["latest"]["status"] != "healthy" or report["admission_gate"]["paused"]:
                        interval = settings["pressure_seconds"]
                deadline = min(last_full + settings["full_seconds"], last_sample + interval)
                if pending_events:
                    deadline = min(deadline, last_sample + settings["event_seconds"])
                if settings["events_enabled"] and active.get("task_id") and subscriptions.memory_path is None:
                    deadline = min(deadline, last_attach + 1)
                pending_events = bool(subscriptions.wait(max(0, deadline - time.monotonic()))) or pending_events
            record(_refresh_resources(readings), subscriptions, "shutdown")
        except (OSError, ValueError, TypeError, KeyError, AttributeError,
                sqlite3.Error, subprocess.SubprocessError) as error:
            # Keep the build result available even if publishing diagnostics fails.
            report["error"] = str(error)
            report["latest"] = {
                "measured_at": datetime.now(timezone.utc).isoformat(),
                "status": "unknown", "reason": "monitor_failed",
            }
            report["events"].append(report["latest"])
            try:
                _write_json(active_path, {**active, "resource_monitor": report})
            except OSError:
                pass
        finally:
            if subscriptions is not None:
                subscriptions.close()

    monitor = threading.Thread(target=sample, name="worker-resource-monitor", daemon=True)
    monitor.start()
    try:
        yield report
    finally:
        stopped.set()
        if wake_source:
            wake_source[0].wake()
        monitor.join()


def _receive(request):
    """Execute one request under the receiving node's capacity policy."""
    operation = request["operation"]
    if operation not in ("capabilities", "diagnostics", "submit"):
        raise ValueError("unknown operation")

    platform = openbuildnet.docker(
        "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}",
        openbuildnet.IMAGE, capture_output=True, text=True, timeout=15,
    ).stdout.strip()
    openbuildnet.STATE.mkdir(parents=True, exist_ok=True)
    active_path = openbuildnet.STATE / "peer-active.json"
    with closing(sqlite3.connect(openbuildnet.STATE / "peer.sqlite3", timeout=0)) as capacity:
        try:
            capacity.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as error:
            if str(error) != "database is locked":
                raise
            available = 0
        else:
            available = 1

        if operation in ("capabilities", "diagnostics"):
            response = {"platform": platform, "image": openbuildnet.IMAGE, "available": available}
            if operation == "diagnostics":
                resources = _measure_resources()
                reserves = _system_reserves()
                gate = _admission_gate(resources, reserves, _monitor_settings()) if resources is not None else None
                rejection = _resource_rejection(resources, reserves) if resources is not None else None
                response["resources"] = resources
                response["resource_health"] = (
                    _resource_health(resources, reserves,
                                     warning_margin_bytes=_monitor_settings()["recovery_margin_bytes"])
                    if resources is not None else None
                )
                admission_reason = rejection["reason"] if rejection else None
                if gate and gate["paused"]:
                    admission_reason = gate["reason"]
                if not available:
                    admission_reason = "busy"
                response["admission"] = {
                    "accepting": bool(available) and admission_reason is None,
                    "reason": admission_reason,
                }
                response["admission_gate"] = gate
                try:
                    response["worker_monitor"] = json.loads(
                        (openbuildnet.STATE / "peer-health.json").read_text(encoding="utf-8")
                    )
                    monitor = response["worker_monitor"]["resource_monitor"]
                    measured = datetime.fromisoformat(monitor["latest"]["measured_at"])
                    age = (datetime.now(timezone.utc) - measured).total_seconds()
                    response["worker_monitor"]["stale"] = age > monitor["settings"]["full_seconds"] + 10
                except FileNotFoundError:
                    response["worker_monitor"] = None
                response["active_task"] = None
                if not available:
                    try:
                        response["active_task"] = json.loads(active_path.read_text(encoding="utf-8"))
                    except FileNotFoundError:
                        # A slot may be reserved before the task starts or while its result is returned.
                        pass
            return response
        if not available:
            return {"accepted": False, "reason": "busy"}
        if request["platform"] != platform or request["image"] != openbuildnet.IMAGE:
            return {"accepted": False, "reason": "unsupported environment"}

        resources = _measure_resources()
        reserves = _system_reserves()
        if resources is not None:
            gate = _admission_gate(resources, reserves, _monitor_settings())
            rejection = _resource_rejection(resources, reserves)
            if rejection:
                return rejection
            if gate["paused"]:
                return {"accepted": False, "reason": gate["reason"]}

        task_id = uuid.uuid4().hex
        active = {"task_id": task_id, "status": "running",
                  "started_at": datetime.now(timezone.utc).isoformat()}
        try:
            _write_json(active_path, active)
            with _monitor_resources(active_path, active, resources, reserves) as report:
                result = openbuildnet.run(request["task"], task_id=task_id)
            if report is not None:
                result["resource_monitor"] = report
                _write_json(openbuildnet.STATE / task_id / "result.json", result)
        finally:
            active_path.unlink(missing_ok=True)
        artifact = Path(result["artifact"]) if result["artifact"] else None
        with Path(result["log"]).open("rb") as log:
            contents = log.read(MAX_LOG + 1)
        result["log"] = base64.b64encode(contents[:MAX_LOG]).decode("ascii")
        result["log_truncated"] = len(contents) > MAX_LOG
        result["artifact"] = (
            {"name": artifact.name, "contents": base64.b64encode(artifact.read_bytes()).decode("ascii")}
            if artifact else None
        )
        return {"accepted": True, "result": result}


def _exchange(peer, request):
    """Send JSON to the peer command through an authenticated SSH connection."""
    payload = json.dumps(request).encode("utf-8")
    if len(payload) > MAX_REQUEST:
        raise ValueError("request exceeds 1 MiB")
    reply = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "-o", "ConnectTimeout=10", "--", peer, RECEIVE_COMMAND],
        input=payload, stdout=subprocess.PIPE, check=True, timeout=420,
    )
    return json.loads(reply.stdout)


def _save(reply, output):
    """Save logs and artifacts under a new local directory."""
    result = reply["result"]
    directory = Path(tempfile.mkdtemp(prefix="openbuildnet-", dir=output)).resolve()
    log = directory / "build.log"
    log.write_bytes(base64.b64decode(result["log"], validate=True))
    result["log"] = str(log)
    if result["artifact"]:
        artifact = result["artifact"]
        target = directory / "artifacts" / openbuildnet.filename(artifact["name"])
        target.parent.mkdir()
        target.write_bytes(base64.b64decode(artifact["contents"], validate=True))
        result["artifact"] = str(target)
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def main():
    """Handle the peer CLI and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("receive")
    commands.add_parser("capabilities").add_argument("peer")
    commands.add_parser("diagnostics").add_argument("peer")
    commands.add_parser("monitor")
    submit = commands.add_parser("submit")
    submit.add_argument("peer")
    submit.add_argument("task", type=Path)
    submit.add_argument("--platform", required=True)
    submit.add_argument("--output", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        if args.action == "monitor":
            openbuildnet.STATE.mkdir(parents=True, exist_ok=True)
            resources = _measure_resources()
            if resources is None:
                raise ValueError("the worker monitor requires Linux")
            with closing(sqlite3.connect(openbuildnet.STATE / "peer-monitor.sqlite3", timeout=0)) as owner:
                owner.execute("BEGIN EXCLUSIVE")
                with _monitor_resources(openbuildnet.STATE / "peer-health.json",
                                        {"role": "worker"}, resources, _system_reserves(), idle=True) as report:
                    while True:
                        time.sleep(1)
                        if "error" in report:
                            raise ValueError(report["error"])
        if args.action == "receive":
            raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST:
                raise ValueError("request exceeds 1 MiB")
            reply = _receive(json.loads(raw))
        else:
            if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.@-]*", args.peer):
                raise ValueError("expected an SSH host alias or user@host")
            request = {"operation": args.action}
            if args.action == "submit":
                if not args.output.is_dir():
                    raise ValueError("output directory must exist")
                request.update(platform=args.platform, image=openbuildnet.IMAGE,
                               task=json.loads(args.task.read_text(encoding="utf-8-sig")))
            reply = _exchange(args.peer, request)
            if args.action == "submit" and reply["accepted"]:
                _save(reply, args.output)
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(reply, indent=2))
    if args.action == "submit":
        return int(not reply["accepted"] or reply["result"]["status"] != "succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
