"""Linux pressure and cgroup event subscriptions for worker resource monitoring."""
import ctypes
import os
from pathlib import Path
import select
import sys
import threading


class LinuxResourceEvents:
    """Own PSI and optional cgroup memory subscriptions.

    Args:
        enabled: Disable subscriptions to use the polling fallback explicitly.

    Call close() to release descriptors. Subscription errors are exposed in errors.
    """

    def __init__(self, enabled=True):
        self.poller = select.poll() if sys.platform == "linux" else None
        self.sources = {}
        self.errors = {}
        self.counts = {}
        self.memory_path = None
        self.memory_counts = {}
        self.wakeup = threading.Event()
        self.wake_write = None
        if self.poller is not None:
            wake_read, self.wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            self.poller.register(wake_read, select.POLLIN)
            self.sources[wake_read] = "wakeup"
        if not enabled or self.poller is None:
            self.errors["events"] = "disabled or unsupported platform"
            return
        for name, metric, trigger in (
            ("psi_memory", "memory", b"some 200000 2000000"),
            ("psi_io", "io", b"full 200000 2000000"),
        ):
            descriptor = None
            try:
                descriptor = os.open(f"/proc/pressure/{metric}", os.O_RDWR | os.O_NONBLOCK)
                os.write(descriptor, trigger + b"\0")
                self.poller.register(descriptor, select.POLLPRI | select.POLLERR)
                self.sources[descriptor] = name
            except OSError as error:
                if descriptor is not None:
                    os.close(descriptor)
                self.errors[name] = str(error)

    def watch_memory(self, path, baseline=None):
        """Attach to a container memory.events file and collect its existing counts."""
        if self.memory_path == path:
            return
        descriptor = None
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            descriptor = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            if descriptor < 0:
                raise OSError(ctypes.get_errno(), "inotify_init1 failed")
            # IN_MODIFY | IN_DELETE_SELF. The kernel also delivers IN_IGNORED.
            if libc.inotify_add_watch(descriptor, os.fsencode(path), 0x2 | 0x400) < 0:
                raise OSError(ctypes.get_errno(), "inotify_add_watch failed")
            self.poller.register(descriptor, select.POLLIN | select.POLLERR)
            self.sources[descriptor] = "memory_events"
            self.memory_path = path
            self.memory_counts = dict(baseline or {})
            self._read_memory_counts()
            self.errors.pop("memory_events", None)
        except (OSError, AttributeError) as error:
            if descriptor is not None and descriptor >= 0:
                self._close_descriptor(descriptor)
            self.errors["memory_events"] = str(error)

    def _read_memory_counts(self):
        """Accumulate counter deltas, including events before the watch was attached."""
        counters = dict(line.split() for line in self.memory_path.read_text(encoding="ascii").splitlines())
        for name, value in counters.items():
            value = int(value)
            delta = max(0, value - self.memory_counts.get(name, 0))
            if delta:
                key = f"memory_events.{name}"
                self.counts[key] = self.counts.get(key, 0) + delta
            self.memory_counts[name] = value

    def wait(self, seconds):
        """Wait for events and return their sources, retaining cumulative counters."""
        if self.poller is None:
            self.wakeup.wait(seconds)
            self.wakeup.clear()
            return set()
        triggered = set()
        for descriptor, flags in self.poller.poll(max(0, int(seconds * 1000))):
            name = self.sources[descriptor]
            try:
                if name == "wakeup":
                    os.read(descriptor, 4096)
                    continue
                if flags & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                    raise OSError("event source disconnected")
                if name == "memory_events":
                    os.read(descriptor, 65536)
                    self._read_memory_counts()
                else:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    os.read(descriptor, 4096)
                    self.counts[name] = self.counts.get(name, 0) + 1
                triggered.add(name)
            except FileNotFoundError:
                # A finished container can remove its cgroup before final inspection.
                self._close_descriptor(descriptor)
                self.memory_path = None
            except OSError as error:
                self.errors[name] = str(error)
                self._close_descriptor(descriptor)
        return triggered

    def wake(self):
        """Interrupt a long idle wait so shutdown does not wait for the full interval."""
        self.wakeup.set()
        if self.wake_write is not None:
            try:
                os.write(self.wake_write, b"x")
            except OSError:
                pass

    def _close_descriptor(self, descriptor):
        if descriptor in self.sources:
            self.poller.unregister(descriptor)
            del self.sources[descriptor]
        os.close(descriptor)

    def close(self):
        """Release every subscription, including partially initialized sources."""
        for descriptor in list(self.sources):
            self._close_descriptor(descriptor)
        if self.wake_write is not None:
            os.close(self.wake_write)
            self.wake_write = None
