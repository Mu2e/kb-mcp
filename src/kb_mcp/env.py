"""Resolution and loading of the ``.env`` file that feeds :mod:`kb_mcp.config`.

Import order matters. :mod:`kb_mcp.config` reads ``os.environ`` at *import*
time (``get_server_config()`` is called at module level by
``kb_mcp.server.server``), so the env file has to be loaded before
``kb_mcp.config`` is first imported. That is why the entry points call
:func:`load_env` at the very top of the module, above their other imports,
rather than from ``main()``.

Resolution order, first hit wins:

1. An explicit path -- ``kb-server --env-file <path>``.
2. ``KB_ENV_FILE`` in the environment. This is the form a ``systemd`` unit
   should use (``Environment=KB_ENV_FILE=...``), since it needs no argv.
3. ``find_dotenv()`` -- walks up from the current working directory. This is
   what makes a plain ``kb-server`` work from inside a checkout.
4. The repository root relative to this file. Only resolves in a source or
   editable layout (``<repo>/src/kb_mcp/env.py``); in a real
   ``pip install`` the package lives under ``site-packages`` and this path
   does not exist, so it is skipped.

Step 4 replaces the older ``Path(__file__).parent.parent.parent.parent``
guess that each entry point used to make. In a non-editable install that
expression resolved to ``<venv>/lib/python3.X/.env`` -- a path that never
exists -- so the deployed servers silently loaded no env file at all.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv, find_dotenv

__all__ = ["resolve_env_file", "load_env", "env_file_from_argv"]

#: Environment variable naming the env file, for callers that cannot pass argv.
ENV_FILE_VAR = "KB_ENV_FILE"


def resolve_env_file(explicit: Optional[str] = None) -> Optional[Path]:
    """Return the env file to load, or ``None`` if there is nothing to load.

    An *explicit* path that does not exist is returned anyway, so the caller
    can report it rather than silently falling through to another file: being
    pointed at the wrong env file is much harder to debug than being told the
    path is missing.
    """
    if explicit:
        return Path(explicit).expanduser()

    from_env = os.environ.get(ENV_FILE_VAR)
    if from_env:
        return Path(from_env).expanduser()

    found = find_dotenv(usecwd=True)
    if found:
        return Path(found)

    # Source/editable layout only: <repo>/src/kb_mcp/env.py -> <repo>/.env
    legacy = Path(__file__).resolve().parents[2] / ".env"
    if legacy.is_file():
        return legacy

    return None


def load_env(explicit: Optional[str] = None) -> Optional[Path]:
    """Load the resolved env file into ``os.environ``. Returns the path used.

    Uses ``override=True`` to match :mod:`kb_mcp.config`, which loads ``.env``
    the same way so that file values beat inherited shell variables.

    Note for deployments: because the file overrides the ambient environment,
    a stray ``.env`` picked up by step 3 of the resolution order would win
    over a systemd unit's ``Environment=``/``EnvironmentFile=`` settings. Set
    ``KB_ENV_FILE`` explicitly in the unit to pin which file is authoritative.
    """
    env_file = resolve_env_file(explicit)
    if env_file is None:
        return None

    if not env_file.is_file():
        if explicit or os.environ.get(ENV_FILE_VAR):
            raise FileNotFoundError(f"env file not found: {env_file}")
        return None

    load_dotenv(dotenv_path=str(env_file), override=True)
    return env_file


def env_file_from_argv(argv=None) -> Optional[str]:
    """Pull ``--env-file`` out of argv before argparse gets a chance to run.

    The real parser in ``main()`` declares the flag too, so it still shows up
    in ``--help`` and is validated there; this scan exists only because the
    value is needed at import time, long before ``main()`` is reached.
    Unknown/other arguments are ignored -- this is deliberately not a parser.
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    for i, arg in enumerate(args):
        if arg == "--env-file":
            return args[i + 1] if i + 1 < len(args) else None
        if arg.startswith("--env-file="):
            return arg.split("=", 1)[1]
    return None
