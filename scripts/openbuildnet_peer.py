"""Exchange build tasks with a manually configured SSH peer."""
import argparse
import base64
from contextlib import closing
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
import uuid

import openbuildnet

MAX_REQUEST = 1024 * 1024
MAX_LOG = 1024 * 1024
MIB = 1024 * 1024
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


def _resource_rejection(resources, reserves):
    """Check one fixed-profile build against fresh readings and system reserves."""
    pools = {}
    for resource, reading in resources.items():
        scope = reading["scope"]
        if reading["measurement_status"] != "ok" or scope is None:
            return {"accepted": False, "reason": "resource_unavailable", "resource": resource}
        if resource == "memory":
            if reading["available_bytes"] < reserves["memory"] + 128 * MIB:
                return {"accepted": False, "reason": "insufficient_memory", "resource": resource}
            continue
        pool = pools.setdefault(scope, {"available": reading["available_bytes"], "reserve": 0,
                                        "demands": {}, "resources": []})
        pool["available"] = min(pool["available"], reading["available_bytes"])
        pool["resources"].append(resource)
        if resource == "snapshot_disk":
            policy, demand, purpose = "snapshot_disk", 4 * MIB, "snapshot"
        elif resource == "state_disk":
            policy, demand, purpose = "persistent_disk", 16 * MIB, "state"
        else:
            # Docker and containerd may report the same backing store.
            policy, demand, purpose = "persistent_disk", 128 * MIB, "runtime"
        pool["reserve"] = max(pool["reserve"], reserves[policy])
        pool["demands"][purpose] = demand

    for scope, pool in pools.items():
        if pool["available"] < pool["reserve"] + sum(pool["demands"].values()):
            return {"accepted": False, "reason": "insufficient_disk",
                    "scope": scope, "resources": pool["resources"]}
    return None


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
    resources = _measure_resources() if operation == "diagnostics" else None
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
                response["resources"] = resources
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
        if resources is not None:
            rejection = _resource_rejection(resources, _system_reserves())
            if rejection:
                return rejection

        task_id = uuid.uuid4().hex
        active = {"task_id": task_id, "status": "running",
                  "started_at": datetime.now(timezone.utc).isoformat()}
        pending = active_path.with_suffix(".tmp")
        pending.write_text(json.dumps(active), encoding="utf-8")
        pending.replace(active_path)
        try:
            result = openbuildnet.run(request["task"], task_id=task_id)
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
    submit = commands.add_parser("submit")
    submit.add_argument("peer")
    submit.add_argument("task", type=Path)
    submit.add_argument("--platform", required=True)
    submit.add_argument("--output", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
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
