"""Writing files that hold credentials.

API keys and session stores are bearer credentials: anyone who can read them
can authenticate as the holder. A plain ``open(path, "w")`` creates the file
with the process umask, which on a typical login node is 022 -- so the keys
landed world-readable on a shared filesystem, where every other account on the
machine could read them and, with a writable directory, replace them.

Everything here writes through a temporary file in the same directory that is
created 0600 and then atomically renamed over the target. That gives three
properties the previous code did not have:

* the file is never visible at a permissive mode, not even briefly;
* a reader either sees the whole previous file or the whole new one, never a
  half-written one -- the old code truncated in place, so a concurrent reader
  could see an empty or partial file;
* a crash mid-write leaves the previous contents intact.
"""

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Union

logger = logging.getLogger(__name__)

#: Owner read/write only. These files are bearer credentials.
SECRET_FILE_MODE = 0o600

#: Owner-only directory, used when *creating* a directory for such files.
SECRET_DIR_MODE = 0o700

__all__ = [
    "SECRET_FILE_MODE",
    "SECRET_DIR_MODE",
    "ensure_private_dir",
    "harden_file",
    "write_private_json",
]


def ensure_private_dir(path: Union[str, Path]) -> Path:
    """Create *path* (and parents) owner-only if it does not already exist.

    An existing directory is left alone. Its mode may have been chosen
    deliberately -- a deployment might share a data directory with a group --
    and silently tightening it could break a running service. The files inside
    are what carry the secrets, and those are always written 0600.
    """
    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True, mode=SECRET_DIR_MODE)
    return path


def harden_file(path: Union[str, Path]) -> None:
    """Tighten an existing credential file to 0600 if it is more permissive.

    This repairs files created before this module existed, or by an older
    release, without needing an operator to notice and fix them by hand.
    """
    path = Path(path)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if mode & 0o077:
        try:
            path.chmod(SECRET_FILE_MODE)
            logger.warning(
                "Tightened permissions on %s from %o to %o: it holds "
                "credentials and was readable by other accounts.",
                path,
                mode,
                SECRET_FILE_MODE,
            )
        except OSError as exc:
            logger.error("Could not tighten permissions on %s: %s", path, exc)


def write_private_json(path: Union[str, Path], data: Any, **dump_kwargs) -> None:
    """Atomically write *data* as JSON to *path*, owner-readable only."""
    path = Path(path)
    ensure_private_dir(path.parent)

    # The temporary file must share a directory with the target so that
    # os.replace() is a same-filesystem rename, which is atomic.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(fd, SECRET_FILE_MODE)
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, **dump_kwargs)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
