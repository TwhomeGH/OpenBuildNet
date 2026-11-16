"""Run and inspect local OpenBuildNet tasks."""
import argparse
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid

IMAGE = "openbuildnet-dev:v0.1"
STATE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OpenBuildNet" / "tasks"


def filename(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*(?:\.[a-zA-Z0-9_-]+)*", value):
        raise ValueError("expected a plain filename")
    if PureWindowsPath(value).is_reserved():
        raise ValueError("reserved filename")
    return value


def docker(*arguments, **options):
    return subprocess.run(["docker", *arguments], check=True, **options)


def collect(container, name, directory):
    directory.mkdir()
    with tempfile.TemporaryFile() as archive:
        docker("cp", f"{container}:/tmp/{name}", "-", stdout=archive, timeout=15)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as bundle:
            member = bundle.next()
            if member is None or not member.isfile() or member.name != name or member.size > 8 * 1024 * 1024:
                raise ValueError("expected one regular artifact of at most 8 MiB")
            with bundle.extractfile(member) as source, (directory / name).open("xb") as target:
                shutil.copyfileobj(source, target)


def run(task):
    command = task["command"]
    timeout = task["timeout_seconds"]
    artifact = filename(task["artifact"])
    if not isinstance(command, list) or not command or not command[0] or not all(
        isinstance(arg, str) and "\0" not in arg for arg in command
    ):
        raise ValueError("command must be a nonempty argument array")
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError("timeout_seconds must be an integer from 1 to 300")
    inputs = {filename(name): contents.encode("utf-8") for name, contents in task["input"].items()}

    task_id = uuid.uuid4().hex
    container = f"openbuildnet-{task_id}"
    directory = STATE / task_id
    directory.mkdir(parents=True)
    log_path = directory / "build.log"
    result = {"task_id": task_id, "status": "running", "exit_code": None,
              "log": str(log_path), "artifact": None}
    result_path = directory / "result.json"

    def save():
        pending = result_path.with_suffix(".tmp")
        pending.write_text(json.dumps(result, indent=2), encoding="utf-8")
        pending.replace(result_path)

    save()
    created = False
    try:
        with log_path.open("wb") as log, tempfile.TemporaryDirectory() as snapshot:
            docker("create", "--pull", "never", "--name", container, "--network", "none",
                   "--memory", "128m", "--cpus", "1", "--pids-limit", "32",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--workdir", "/tmp", "--entrypoint", command[0], IMAGE, *command[1:],
                   stdout=subprocess.DEVNULL, stderr=log, timeout=15)
            created = True
            for name, contents in inputs.items():
                (Path(snapshot) / name).write_bytes(contents)
            docker("cp", f"{snapshot}{os.sep}.", f"{container}:/tmp",
                   stdout=log, stderr=log, timeout=15)
            try:
                subprocess.run(["docker", "start", "--attach", container], stdout=log, stderr=log,
                               timeout=timeout)
            except subprocess.TimeoutExpired:
                docker("kill", container, stdout=subprocess.DEVNULL, stderr=log, timeout=15)
                result["status"] = "timed_out"
                return result
            state = json.loads(docker("inspect", "--format", "{{json .State}}", container,
                                      capture_output=True, timeout=15).stdout)
            if state["Status"] != "exited" or state["Error"]:
                raise ValueError(state["Error"] or "container did not reach the exited state")
            result["exit_code"] = state["ExitCode"]
            if result["exit_code"] == 0:
                collect(container, artifact, directory / "artifacts")
                result["artifact"] = str(directory / "artifacts" / artifact)
                result["status"] = "succeeded"
            else:
                result["status"] = "failed"
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        result.update(status="failed", error=str(error))
    finally:
        if created:
            try:
                docker("rm", "--force", container, capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError) as error:
                result["cleanup_error"] = str(error)
        save()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("run").add_argument("task", type=Path)
    commands.add_parser("show").add_argument("task_id")
    args = parser.parse_args()
    try:
        if args.action == "run":
            result = run(json.loads(args.task.read_text(encoding="utf-8-sig")))
        else:
            if not re.fullmatch(r"[0-9a-f]{32}", args.task_id):
                raise ValueError("invalid task ID")
            result = json.loads((STATE / args.task_id / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(result, indent=2))
    return int(args.action == "run" and result["status"] != "succeeded")


if __name__ == "__main__":
    raise SystemExit(main())
