"""Single-server-instance lockfile (POSIX flock; msvcrt region lock on Windows).

Reverse-engineered from ``main.tsx:3982`` which imports
``./server/lockfile.js``. The TS source isn't in this snapshot; the
contract from context is: prevent two ``claude server`` invocations
from competing for the same ``--port`` (or default port).

POSIX ``fcntl.flock(LOCK_EX | LOCK_NB)`` is automatically released by
the kernel when the holding process exits, so stale-lock-after-crash
is automatic.

Windows uses ``msvcrt.locking(LK_NBLCK)`` on one byte of the same file.
NT region locks are likewise dropped by the kernel when the holding
process dies, so both platforms share the crash-recovery guarantee —
no stale lockfile can wedge a restart.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class LockfileBusyError(RuntimeError):
    """Another process holds the server lockfile."""


class ServerLockfile:
    """Acquire/release context for ``~/.clawcodex/server.lock``.

    Usage:
        async with ServerLockfile(path).hold():
            await server.serve_forever()

    Or imperatively:
        lock = ServerLockfile(path)
        lock.acquire()  # raises LockfileBusyError if held
        try:
            ...
        finally:
            lock.release()
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        """Acquire the lock (LOCK_EX | LOCK_NB).

        Raises ``LockfileBusyError`` if another process holds it.
        ``OSError`` on filesystem failures (no permissions, missing
        parent directory after the mkdir attempt, etc.) — those
        propagate so the caller can decide how to surface.
        """
        try:
            import fcntl
        except ImportError:
            # Windows: exclusive msvcrt region lock on 1 byte at offset 0
            # (a fresh fd sits at offset 0; locking past EOF is legal on
            # NT, so an empty lockfile locks fine). ``LK_NBLCK`` fails
            # immediately instead of the LK_LOCK 10×1s retry dance —
            # mirroring flock's LOCK_NB. Like flock, the kernel releases
            # the region when the holding process dies, so
            # stale-lock-after-crash recovery matches the POSIX path.
            import msvcrt

            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                # A held region lock surfaces as EACCES — same meaning
                # as BlockingIOError on the flock path below.
                os.close(fd)
                raise LockfileBusyError(
                    f'Another claude server instance holds {self._path}'
                ) from exc
            self._fd = fd
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise LockfileBusyError(
                f'Another claude server instance holds {self._path}'
            ) from exc
        self._fd = fd

    def release(self) -> None:
        """Release the lock and close the FD. Idempotent."""
        if self._fd is None:
            return
        try:
            import fcntl

            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
        except ImportError:
            # Windows: explicitly LK_UNLCK the byte locked in acquire()
            # before closing. Closing the fd would drop the region too,
            # but NT documents that implicitly-released locks may linger
            # briefly — explicit unlock keeps re-acquire immediate.
            # ``lseek`` back to 0 because unlock must target the same
            # offset the lock was taken at.
            try:
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            except (ImportError, OSError):
                pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        # Best-effort unlink so the file doesn't accumulate over restarts.
        # On Windows this raises PermissionError (an OSError) when another
        # process still has the file open — fine: the region lock is gone,
        # so the next acquire() reuses the existing file and locks it.
        try:
            self._path.unlink()
        except OSError:
            pass

    def __enter__(self) -> ServerLockfile:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


__all__ = ['LockfileBusyError', 'ServerLockfile']
