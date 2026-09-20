"""Best-effort recording of import runs in the `logs_import_runs` table.

`Source.process_all()` calls `start_run()` when a run begins, `update_run()`
periodically while it works (so a long run shows live counts), and
`finish_run()` when it ends; `kb logs imports` reads the rows back. Every
write also records the process's peak memory so far in meta["peak_rss_mb"].
All of them are best-effort: a database hiccup while recording is logged and
swallowed, never raised, so bookkeeping can't fail an import that would
otherwise succeed.
"""

import logging
import os
import resource
import socket
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def start_run(source_id: str, params: Dict[str, Any]) -> Optional[str]:
    """Insert a "running" row for a new import run and return its id (None on failure)."""
    try:
        from ..kb.database import get_db_session
        from ..kb.db_models import ImportRun

        with get_db_session() as session:
            run = ImportRun(
                source_id=source_id,
                status="running",
                started_time=datetime.now(timezone.utc),
                hostname=socket.gethostname(),
                pid=os.getpid(),
                trigger=os.getenv("KB_RUN_TRIGGER") or "manual",
                log_path=os.getenv("KB_RUN_LOG"),
                args={"argv": sys.argv, **params},
            )
            session.add(run)
            session.commit()
            return run.id
    except Exception as e:
        logger.warning(f"Could not record start of import run: {e}")
        return None


def peak_rss_mb() -> int:
    """Peak resident memory of this process so far, in MB (Linux reports ru_maxrss in KB)."""
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


def update_run(run_id: Optional[str], **fields: Any) -> None:
    """Record progress on a still-running import run (ImportRun column values)."""
    _write(run_id, fields, what="progress")


def finish_run(run_id: Optional[str], status: str, **fields: Any) -> None:
    """Mark an import run finished with `status` and the given ImportRun column values."""
    _write(run_id, {**fields, "status": status, "finished_time": datetime.now(timezone.utc)}, what="end")


def _write(run_id: Optional[str], fields: Dict[str, Any], what: str) -> None:
    if run_id is None:
        return
    try:
        from ..kb.database import get_db_session
        from ..kb.db_models import ImportRun

        with get_db_session() as session:
            run = session.get(ImportRun, run_id)
            if run is None:
                logger.warning(f"Import run {run_id} not found; cannot record its {what}")
                return
            for key, value in fields.items():
                if key == "meta":
                    continue
                # Fresh containers, so SQLAlchemy sees JSON columns as changed
                # even when the caller keeps mutating its own list.
                setattr(run, key, list(value) if isinstance(value, list) else value)
            run.meta = {**(run.meta or {}), **(fields.get("meta") or {}), "peak_rss_mb": peak_rss_mb()}
            session.commit()
    except Exception as e:
        logger.warning(f"Could not record {what} of import run {run_id}: {e}")
