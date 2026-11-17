"""Exchange build tasks with a manually configured SSH peer."""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid

import openbuildnet

MAX_REQUEST = 1024 * 1024
MAX_LOG = 1024 * 1024
RECEIVE_COMMAND = (
    'if [ -x "$HOME/.local/bin/openbuildnet-peer" ]; then '
    'exec "$HOME/.local/bin/openbuildnet-peer" receive; '
    'else exec openbuildnet-peer receive; fi'
)


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
