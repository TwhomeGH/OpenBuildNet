"""Regression checks for resource pressure, admission and monitoring cleanup."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import openbuildnet_peer as peer
from worker_events import LinuxResourceEvents

TEST_SETTINGS = {
    "active_seconds": 0.01, "pressure_seconds": 0.01, "full_seconds": 0.1,
    "event_seconds": 0.005, "recovery_seconds": 0, "recovery_margin_bytes": 0,
    "events_enabled": False,
}


def resources(memory_mib=1024):
    """Return synthetic readings without consuming host memory or disk."""
    return {
        "memory": {"scope": "ram", "measurement_status": "ok",
                   "available_bytes": memory_mib * peer.MIB},
        "state_disk": {"scope": "disk", "measurement_status": "ok",
                       "available_bytes": 4096 * peer.MIB},
        "runtime_disk": {"scope": "disk", "measurement_status": "ok",
                         "available_bytes": 4096 * peer.MIB},
        "snapshot_disk": {"scope": "tmp", "measurement_status": "ok",
                          "available_bytes": 128 * peer.MIB},
    }


class ResourceMonitoringTests(unittest.TestCase):
    def test_event_storm_keeps_full_checks_and_closes_subscriptions(self):
        """Coalesced events cannot postpone full checks, and rebinds release handles."""
        instances = []

        class Subscriptions:
            send_events = True

            def __init__(self, enabled):
                self.poller = True
                self.sources = {1: "psi_memory"}
                self.errors = {}
                self.counts = {}
                self.memory_path = None
                self.memory_counts = {}
                self.stopped = threading.Event()
                self.closed = False
                instances.append(self)

            def wait(self, seconds):
                if not self.send_events:
                    self.stopped.wait(seconds)
                    return set()
                self.stopped.wait(min(seconds, 0.005))
                self.counts["psi_memory"] = self.counts.get("psi_memory", 0) + 1
                return {"psi_memory"}

            def wake(self):
                self.stopped.set()

            def close(self):
                self.closed = True

        settings = {**TEST_SETTINGS, "events_enabled": True, "full_seconds": 0.08,
                    "event_seconds": 0.02, "active_seconds": 1}
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(peer.openbuildnet, "STATE", Path(temporary)),
            patch.object(peer, "LinuxResourceEvents", Subscriptions),
            patch.object(peer, "_monitor_settings", return_value=settings),
            patch.object(peer, "_measure_resources", side_effect=resources) as full,
            patch.object(peer, "_refresh_resources", side_effect=lambda previous: resources()) as light,
        ):
            with peer._monitor_resources(Path(temporary) / "health.json", {}, resources(),
                                         peer.DEFAULT_RESERVES, idle=True) as report:
                deadline = time.monotonic() + 2
                while full.call_count < 2 and time.monotonic() < deadline:
                    time.sleep(0.005)
            self.assertGreaterEqual(full.call_count, 2)
            self.assertGreater(light.call_count, 1)
            self.assertEqual(len(report["events"]), 1)
            self.assertGreater(report["event_counts"]["psi_memory"], light.call_count)
            self.assertTrue(all(subscription.closed for subscription in instances))
            Subscriptions.send_events = False
            full.reset_mock()
            light.reset_mock()
            with peer._monitor_resources(Path(temporary) / "health.json", {}, resources(),
                                         peer.DEFAULT_RESERVES, idle=True):
                deadline = time.monotonic() + 2
                while full.call_count < 2 and time.monotonic() < deadline:
                    time.sleep(0.005)
            self.assertGreaterEqual(full.call_count, 2)
            self.assertEqual(light.call_count, 1)  # Final shutdown reading only.
            self.assertTrue(all(subscription.closed for subscription in instances))

    def test_idle_fallback_uses_short_polling(self):
        """Unavailable subscriptions must not leave idle checks on the long interval."""
        settings = {**TEST_SETTINGS, "full_seconds": 60, "active_seconds": 0.02}
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(peer.openbuildnet, "STATE", Path(temporary)),
            patch.object(peer, "_monitor_settings", return_value=settings),
            patch.object(peer, "_refresh_resources", side_effect=lambda previous: resources()) as light,
        ):
            with peer._monitor_resources(Path(temporary) / "health.json", {}, resources(),
                                         peer.DEFAULT_RESERVES, idle=True) as report:
                deadline = time.monotonic() + 2
                while light.call_count < 2 and time.monotonic() < deadline:
                    time.sleep(0.005)
            self.assertGreaterEqual(light.call_count, 2)
            self.assertEqual(report["mode"], "polling_fallback")
            self.assertIn("events", report["subscription_errors"])

    def test_pressure_recovery_and_result_persistence(self):
        """Observe transitions during a task, then verify a fresh request can enter."""
        current = {"readings": resources()}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active_path = root / "peer-active.json"

            def wait_for_health(status):
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    active = json.loads(active_path.read_text(encoding="utf-8"))
                    if active.get("resource_monitor", {}).get("latest", {}).get("status") == status:
                        return active
                    time.sleep(0.005)
                self.fail(f"monitor did not publish {status}")

            def run_task(task, *, task_id):
                directory = root / task_id
                directory.mkdir()
                log = directory / "build.log"
                log.write_bytes(b"build-complete\n")
                if task.get("pressure"):
                    current["readings"] = resources(128)
                    wait_for_health("pressure")
                    observed = peer._receive({"operation": "diagnostics"})
                    self.assertFalse(observed["admission"]["accepting"])
                    self.assertEqual(observed["resource_health"]["status"], "pressure")
                    current["readings"] = resources()
                    wait_for_health("healthy")
                return {"task_id": task_id, "status": "succeeded", "exit_code": 0,
                        "log": str(log), "artifact": None}

            with (
                patch.object(peer.openbuildnet, "STATE", root),
                patch.object(peer.openbuildnet, "docker", return_value=SimpleNamespace(stdout="linux/amd64")),
                patch.object(peer.openbuildnet, "run", side_effect=run_task),
                patch.object(peer, "_measure_resources", side_effect=lambda: current["readings"]),
                patch.object(peer, "_refresh_resources", side_effect=lambda previous: current["readings"]),
                patch.object(peer, "_system_reserves", return_value=peer.DEFAULT_RESERVES),
                patch.object(peer, "_monitor_settings", return_value=TEST_SETTINGS),
            ):
                request = {"operation": "submit", "platform": "linux/amd64",
                           "image": peer.openbuildnet.IMAGE, "task": {"pressure": True}}
                reply = peer._receive(request)
                result = reply["result"]
                events = result["resource_monitor"]["events"]
                self.assertEqual([event["status"] for event in events],
                                 ["healthy", "pressure", "healthy"])
                saved = json.loads((root / result["task_id"] / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["resource_monitor"], result["resource_monitor"])
                self.assertFalse(active_path.exists())

                current["readings"] = resources(128)
                rejected = peer._receive(request)
                self.assertEqual(rejected["reason"], "insufficient_memory")
                self.assertFalse(peer._receive({"operation": "diagnostics"})["admission"]["accepting"])
                current["readings"] = resources()
                self.assertTrue(peer._receive({"operation": "diagnostics"})["admission"]["accepting"])
                self.assertTrue(peer._receive({**request, "task": {}})["accepted"])

    def test_disk_pressure_and_unavailable_measurement(self):
        """Detect disk pressure separately from the capacity needed by a new task."""
        readings = resources()
        readings["state_disk"]["available_bytes"] = 1023 * peer.MIB
        health = peer._resource_health(readings, peer.DEFAULT_RESERVES)
        self.assertEqual(health["reason"], "insufficient_disk")
        readings["state_disk"]["available_bytes"] = 1024 * peer.MIB
        self.assertEqual(peer._resource_health(readings, peer.DEFAULT_RESERVES)["status"], "healthy")
        self.assertEqual(peer._resource_rejection(readings, peer.DEFAULT_RESERVES)["reason"],
                         "insufficient_disk")
        readings["memory"].update(measurement_status="unknown", available_bytes=None)
        self.assertEqual(peer._resource_health(readings, peer.DEFAULT_RESERVES)["status"], "unknown")

    def test_monitor_failure_is_visible(self):
        """Report a failed probe without leaving the last healthy reading as current."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "active.json"
            with (
                patch.object(peer.openbuildnet, "STATE", Path(temporary)),
                patch.object(peer, "_measure_resources", side_effect=OSError("probe failed")),
                patch.object(peer, "_refresh_resources", side_effect=OSError("probe failed")),
                patch.object(peer, "_monitor_settings", return_value=TEST_SETTINGS),
                peer._monitor_resources(path, {"task_id": "test"}, resources(),
                                        peer.DEFAULT_RESERVES) as report,
            ):
                deadline = time.monotonic() + 2
                while "error" not in report and time.monotonic() < deadline:
                    time.sleep(0.005)
            self.assertEqual(report["error"], "probe failed")
            self.assertEqual(report["latest"]["status"], "unknown")
            self.assertEqual(report["latest"]["reason"], "monitor_failed")

    def test_recovery_requires_margin_and_stable_time(self):
        """A new SSH request must honor the persisted recovery window."""
        settings = {**TEST_SETTINGS, "recovery_seconds": 30, "recovery_margin_bytes": 64 * peer.MIB}
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            peer.openbuildnet, "STATE", Path(temporary)
        ):
            with patch.object(peer.time, "time", return_value=100):
                self.assertTrue(peer._admission_gate(resources(128), peer.DEFAULT_RESERVES, settings)["paused"])
            with patch.object(peer.time, "time", return_value=110):
                self.assertIsNone(peer._admission_gate(resources(300), peer.DEFAULT_RESERVES, settings)["recovery_since"])
                self.assertTrue(peer._admission_gate(resources(), peer.DEFAULT_RESERVES, settings)["paused"])
            with patch.object(peer.time, "time", return_value=139):
                self.assertTrue(peer._admission_gate(resources(), peer.DEFAULT_RESERVES, settings)["paused"])
            with patch.object(peer.time, "time", return_value=140):
                self.assertFalse(peer._admission_gate(resources(), peer.DEFAULT_RESERVES, settings)["paused"])

    @unittest.skipUnless(peer.sys.platform == "linux", "requires Linux inotify")
    def test_memory_event_counts_survive_subscription_rebind(self):
        """Rebinding must preserve counts without counting the same OOM twice."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "memory.events"
            path.write_text("max 2\noom 1\noom_kill 1\n", encoding="ascii")
            first = LinuxResourceEvents(enabled=False)
            second = LinuxResourceEvents(enabled=False)
            try:
                first.watch_memory(path)
                baseline = dict(first.memory_counts)
                counts = dict(first.counts)
                first.close()
                second.counts.update(counts)
                second.watch_memory(path, baseline)
                self.assertEqual(second.counts, counts)
                path.write_text("max 3\noom 2\noom_kill 2\n", encoding="ascii")
                self.assertIn("memory_events", second.wait(1))
                self.assertEqual(second.counts["memory_events.oom_kill"], 2)
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    unittest.main()
