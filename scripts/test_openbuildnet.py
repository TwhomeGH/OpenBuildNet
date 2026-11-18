"""End-to-end checks for the local runner using the development Docker image."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import openbuildnet

ROOT = Path(__file__).resolve().parents[1]


class LocalRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.env = dict(os.environ, LOCALAPPDATA=str(self.directory))

    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/openbuildnet.py"), *arguments],
            env=self.env, capture_output=True, text=True, timeout=90,
        )

    def test_snapshot_limits_before_container_creation(self):
        """Reject excessive snapshots before allocating disk space or a container."""
        example = json.loads(
            (ROOT / "examples/single-node-task.json").read_text(encoding="utf-8-sig")
        )
        for inputs in (
            {f"file{number}.txt": "" for number in range(65)},
            {"input.txt": "x" * (openbuildnet.MAX_INPUT_BYTES + 1)},
        ):
            with (
                patch.object(openbuildnet, "STATE", self.directory / "tasks"),
                patch.object(openbuildnet, "docker") as docker,
                self.assertRaisesRegex(ValueError, "input snapshot exceeds"),
            ):
                openbuildnet.run({**example, "input": inputs})
            docker.assert_not_called()
            self.assertFalse((self.directory / "tasks").exists())

    def test_task_results(self):
        example = json.loads(
            (ROOT / "examples/single-node-task.json").read_text(encoding="utf-8-sig")
        )
        cases = (
            ("success", example["command"], 10, "succeeded", 0, "build-complete"),
            ("failure", ["sh", "-c", "echo build-failed >&2; exit 7"], 10,
             "failed", 7, "build-failed"),
            ("missing_artifact", ["sh", "-c", "echo build-complete"], 10,
             "failed", 0, "build-complete"),
            ("timeout", ["sh", "-c", "echo build-started; sleep 60"], 5,
             "timed_out", None, "build-started"),
            ("oom", ["awk",
                     "BEGIN {s=\"x\"; for(i=0;i<28;i++) s=s s; print length(s)}"],
             20, "failed", 137, ""),
            ("log_limit", ["sh", "-c", "echo log-started; yes output"], 15,
             "failed", None, "log-started"),
            ("artifact_limit", ["sh", "-c",
                                "dd if=/dev/zero of=result.txt bs=1048576 count=9; echo artifact-created"],
             15, "failed", 0, "artifact-created"),
            ("recovery", example["command"], 10, "succeeded", 0, "build-complete"),
        )
        for name, command, timeout, status, code, marker in cases:
            with self.subTest(case=name):
                task_file = self.directory / f"{name}.json"
                task_file.write_text(json.dumps({
                    **example, "command": command, "timeout_seconds": timeout,
                }), encoding="utf-8")
                execution = self.cli("run", str(task_file))
                result = json.loads(execution.stdout)
                self.assertEqual(result["status"], status, result)
                self.assertEqual(execution.returncode, int(status != "succeeded"))
                self.assertEqual(result["exit_code"], code)
                self.assertNotIn("cleanup_error", result)
                if name == "oom":
                    self.assertEqual(result["error"], "memory_limit_exceeded")
                if name == "log_limit":
                    self.assertEqual(result["error"], "log_limit_exceeded")
                    self.assertEqual(Path(result["log"]).stat().st_size, 1024 * 1024)
                if name == "artifact_limit":
                    self.assertEqual(result["error"], "artifact_limit_exceeded")
                inspection = subprocess.run(
                    ["docker", "inspect", f"openbuildnet-{result['task_id']}"],
                    capture_output=True, timeout=15,
                )
                self.assertNotEqual(inspection.returncode, 0)
                if name == "missing_artifact":
                    self.assertIn("error", result)
                self.assertIn(marker, Path(result["log"]).read_text(encoding="utf-8"))
                query = self.cli("show", result["task_id"])
                self.assertEqual(query.returncode, 0, query.stderr)
                self.assertEqual(json.loads(query.stdout), result)
                if status == "succeeded":
                    self.assertEqual(Path(result["artifact"]).read_bytes(), b"OpenBuildNet\n")
                else:
                    self.assertIsNone(result["artifact"])


if __name__ == "__main__":
    try:
        subprocess.run(
            ["docker", "image", "inspect", "openbuildnet-dev:v0.1"],
            stdout=subprocess.DEVNULL, check=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        sys.exit(f"Docker engine and local openbuildnet-dev:v0.1 image required: {error}")
    unittest.main()
