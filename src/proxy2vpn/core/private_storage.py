"""Private, durable file primitives shared by agent storage and identity keys."""

from __future__ import annotations

import os
import re
import unicodedata
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from filelock import FileLock


class StorageError(RuntimeError):
    """Report storage failures without including persisted evidence."""


def reject_symlink_components(path: Path) -> None:
    """Reject lexical symlinks without changing arbitrary parent permissions."""
    for component in (path, *path.parents):
        if component.is_symlink():
            raise StorageError("Agent storage directory must not be a symlink")


def private_directory(path: Path, *, repair_existing: bool = False) -> None:
    """Create a private directory and reject symlink components."""
    reject_symlink_components(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise StorageError("Agent storage directory is not a directory")
    if os.name == "posix" and repair_existing:
        os.chmod(path, 0o700)


def private_file(path: Path, *, create: bool = False, repair: bool = True) -> None:
    """Validate regular files and repair permissions without following links."""
    reject_symlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if create:
        flags |= os.O_CREAT
    try:
        fd = os.open(path, flags, 0o600)
    except FileNotFoundError:
        return
    except OSError:
        raise StorageError("Cannot safely open agent storage file") from None
    try:
        info = os.fstat(fd)
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StorageError("Agent storage requires regular files")
        if os.name == "posix":
            if info.st_uid != os.getuid():
                raise StorageError("Agent storage file must belong to the current user")
            if repair:
                os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def managed_directory(
    path: Path, names: frozenset[str], *, repair: bool = True
) -> None:
    """Validate all artifacts without mutation before repairing a dedicated root."""
    reject_symlink_components(path)
    entries = []
    if path.exists():
        if not path.is_dir():
            raise StorageError("Agent storage directory is not a directory")
        if os.name == "posix" and path.stat().st_uid != os.getuid():
            raise StorageError(
                "Agent storage directory must belong to the current user"
            )
        entries = list(path.iterdir())
    normalized_names = {unicodedata.normalize("NFC", name).casefold() for name in names}
    for artifact in entries:
        normalized = unicodedata.normalize("NFC", artifact.name).casefold()
        temporary = any(
            re.fullmatch(re.escape(name) + r"[a-z0-9_]{8}\.tmp", normalized)
            for name in normalized_names
        )
        legacy_key_temp = re.fullmatch(r"identity\.[a-z0-9_]{8}", normalized)
        if normalized not in normalized_names and not temporary and not legacy_key_temp:
            raise StorageError(
                "Agent storage directory contains unrelated entries; choose a dedicated directory"
            )
        private_file(artifact, repair=False)
    if not repair:
        return
    private_directory(path, repair_existing=True)
    for artifact in entries:
        private_file(artifact)


def private_lock(path: Path, timeout: float = 10) -> FileLock:
    private_directory(path.parent)
    private_file(path, create=True)
    return FileLock(str(path), timeout=timeout, mode=0o600)


def sync_directory(path: Path) -> None:
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write(path: Path, content: bytes) -> None:
    """Replace a file only after syncing private temporary content to disk."""
    private_directory(path.parent)
    private_file(path)
    fd, name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        private_file(path)
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            Path(name).unlink()
