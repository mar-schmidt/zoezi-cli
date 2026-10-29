from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path

from .errors import ZoeziError


def state_root() -> Path:
    return Path(os.environ.get("ZOEZI_CONFIG_DIR", Path.home() / ".config" / "zoezi-cli")).expanduser()


def read_json(path: Path, default=None):
    if path.is_symlink():
        raise ZoeziError("unsafe_state_path", "Refusing a symbolic link for local state.", exit_code=1)
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise ZoeziError("invalid_state", "Cannot read local state.", exit_code=1, path=str(path)) from exc


def write_json(path: Path, data):
    if path.is_symlink():
        raise ZoeziError("unsafe_state_path", "Refusing a symbolic link for local state.", exit_code=1)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".zoezi-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextlib.contextmanager
def state_lock(root: Path, timeout: float = 15):
    """Serialize a complete command, including check/mutate/readback and re-login."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "state.lock"
    if path.is_symlink():
        raise ZoeziError("unsafe_state_path", "Refusing symbolic link for lock.", exit_code=1)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    if os.name == "nt":
        import msvcrt

        os.write(fd, b"0")

        def acquire():
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

        def release():
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def acquire():
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def release():
            fcntl.flock(fd, fcntl.LOCK_UN)
    locked = False
    try:
        deadline = time.monotonic() + timeout
        while not locked:
            try:
                acquire()
                locked = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise ZoeziError("state_locked", "Another Zoezi command is still running.", exit_code=4)
                time.sleep(0.1)
        yield
    finally:
        if locked:
            release()
        os.close(fd)


class CredentialStore:
    """Only persist passwords in an explicitly selected OS keychain."""

    def __init__(self, tenant: str, username: str):
        self.service = "zoezi-cli:" + tenant
        self.username = username

    def _backend(self):
        try:
            import keyring

            if keyring.get_keyring().priority <= 0:
                raise RuntimeError("No backend")
            return keyring
        except Exception as exc:
            raise ZoeziError(
                "keyring_unavailable", "Install zoezi-cli[keyring] and configure an OS keychain; otherwise use ZOEZI_PASSWORD.",
                exit_code=2,
            ) from exc

    def get(self):
        try:
            return self._backend().get_password(self.service, self.username)
        except Exception:
            return None

    def set(self, password):
        try:
            self._backend().set_password(self.service, self.username, password)
        except ZoeziError:
            raise
        except Exception as exc:
            raise ZoeziError("keyring_unavailable", "Cannot save credentials to the OS keychain.", exit_code=2) from exc

    def delete(self):
        try:
            backend = self._backend()
            if backend.get_password(self.service, self.username) is not None:
                backend.delete_password(self.service, self.username)
        except ZoeziError:
            return
