"""Run a subprocess with bounded streamed output and a cancellation deadline."""
import subprocess
import threading
import time


def run_bounded(arguments, target, *, limit_bytes, timeout, limit_error,
                merge_stderr=True, on_abort=None):
    """Stream output to a file, enforcing a byte limit before each write.

    Args:
        arguments: Program and argument array.
        target: Open binary output file, kept open by the caller.
        limit_bytes: Maximum total file size, including existing contents.
        timeout: Execution deadline in seconds.
        limit_error: ValueError message returned on excess output.
        merge_stderr: Capture stderr with stdout, or discard it.
        on_abort: Optional callback to stop the workload behind the subprocess.

    Returns:
        The subprocess exit code.

    Raises:
        ValueError: Output exceeds the configured byte limit.
        OSError: Starting the process or writing its output fails.
        subprocess.TimeoutExpired: The execution deadline is exceeded.
    """
    stopped = threading.Event()
    overflow = threading.Event()
    errors = []
    remaining = max(0, limit_bytes - target.tell())

    with subprocess.Popen(
        arguments, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
    ) as process:
        def copy_output():
            """Drain the pipe while limiting writes to the remaining file budget."""
            nonlocal remaining
            try:
                while True:
                    chunk = process.stdout.read1(65536)
                    if not chunk:
                        break
                    written = min(remaining, len(chunk))
                    if written:
                        target.write(chunk[:written])
                        target.flush()
                        remaining -= written
                    if written < len(chunk):
                        overflow.set()
                        stopped.set()
            except OSError as error:
                errors.append(error)
                stopped.set()

        reader = threading.Thread(target=copy_output, name="bounded-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        try:
            while process.poll() is None:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    timed_out = True
                    break
                if stopped.wait(min(0.05, remaining_time)):
                    break
            if process.poll() is None:
                if on_abort is not None:
                    on_abort()
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join()

        if errors:
            raise errors[0]
        if overflow.is_set():
            raise ValueError(limit_error)
        if timed_out:
            raise subprocess.TimeoutExpired(arguments, timeout)
        return process.returncode
