"""Import runs web page: the scheduled DocDB job's runs and their log files."""

import logging
import re
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from typing import Optional

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse

from ..auth import WebSessionManager
from .. import html_templates

logger = logging.getLogger(__name__)

# Log files the import job writes (kb-docdb-update.sh). Log files without a
# run record are only listed and served if their name matches this exactly.
LOG_NAME_RE = re.compile(r"^docdb-update-\d{8}-\d{6}\.log$")

# Serve at most this much of a log (its end, where the result is).
MAX_LOG_BYTES = 2 * 1024 * 1024

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def allowed_log_path(path: Optional[str], allowed_dirs: list) -> Optional[Path]:
    """Resolve `path` and return it only if it is a file inside an allowed dir.

    Symlinks and `..` are resolved first, so neither can lead outside.
    """
    if not path or not allowed_dirs:
        return None
    try:
        real = Path(path).resolve()
    except (OSError, RuntimeError):
        return None
    if not real.is_file():
        return None
    for d in allowed_dirs:
        if real.is_relative_to(d):
            return real
    return None


def clean_log_text(raw: str) -> str:
    """Make a job log readable in a browser.

    Progress bars redraw a line with carriage returns, which leaves hundreds
    of intermediate states on one line; keep only the last state. Also drop
    terminal colour codes.
    """
    lines = []
    # split("\n"), not splitlines(): splitlines() also breaks on "\r" and
    # would turn every progress-bar redraw into a line of its own.
    for line in raw.split("\n"):
        if "\r" in line:
            line = line.rstrip("\r").rsplit("\r", 1)[-1]
        lines.append(_ANSI_RE.sub("", line))
    return "\n".join(lines)


def read_log_tail(path: Path) -> str:
    """The cleaned log, or its last MAX_LOG_BYTES with a note if longer."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > MAX_LOG_BYTES:
            f.seek(size - MAX_LOG_BYTES)
            data = f.read()
            head = f"[... first {size - MAX_LOG_BYTES} bytes of {size} omitted ...]\n"
        else:
            data = f.read()
            head = ""
    return head + clean_log_text(data.decode("utf-8", errors="replace"))


def _local_time(iso: Optional[str]) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def setup_imports_routes(app, session_manager: WebSessionManager, require_auth_html):
    """Register the import runs page and its log views (admin only)."""
    from ....config import get_import_log_dirs
    from ....kb.logs import get_import_runs

    def _cell(value) -> str:
        return "" if value is None else html_escape(str(value))

    async def web_imports(request: Request):
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        username = session_data.get("username")
        allowed = get_import_log_dirs()

        runs = get_import_runs(limit=200)
        referenced = set()
        rows = []
        for run in runs:
            log_file = allowed_log_path(run.get("log_path"), allowed)
            if log_file:
                referenced.add(log_file)
                log_cell = f'<a href="/web/imports/{html_escape(run["id"])}/log">log</a>'
            elif run.get("log_path"):
                log_cell = f'<span title="{html_escape(run["log_path"])}" style="color:#999;">not readable</span>'
            else:
                log_cell = ""
            status = run.get("status") or ""
            colour = {"success": "#2e7d32", "failed": "#c62828", "partial": "#ef6c00", "running": "#1565c0"}.get(status, "#555")
            errors = (run.get("summarize_errors") or 0) + (run.get("embed_errors") or 0)
            error_text = run.get("error") or ""
            rows.append(
                "<tr>"
                f"<td>{_local_time(run.get('started_time'))}</td>"
                f'<td style="color:{colour}; font-weight:bold;">{html_escape(status)}</td>'
                f"<td>{_cell(run.get('source_id'))}</td>"
                f"<td>{_cell(run.get('hostname'))}</td>"
                f"<td>{_cell(run.get('trigger'))}</td>"
                f"<td>{_duration(run.get('duration_seconds'))}</td>"
                f"<td>{_cell(run.get('items_found'))}</td>"
                f"<td>{_cell(run.get('documents_created'))}</td>"
                f"<td>{_cell(run.get('items_skipped'))}</td>"
                f"<td>{_cell(run.get('items_failed'))}</td>"
                f"<td>{_cell(run.get('summarized'))}</td>"
                f"<td>{_cell(run.get('chunked'))}</td>"
                f"<td>{errors or ''}</td>"
                f'<td title="{html_escape(error_text)}">{html_escape(error_text[:60])}</td>'
                f"<td>{log_cell}</td>"
                "</tr>"
            )

        # Runs that failed before kb-import started (credentials, setup, a
        # missing KB_ENV_FILE) have a log file but no run record.
        orphans = []
        for d_index, d in enumerate(allowed):
            for f in sorted(d.glob("docdb-update-*.log"), reverse=True)[:200]:
                if LOG_NAME_RE.match(f.name) and f.resolve() not in referenced:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
                    orphans.append(
                        f"<tr><td>{_local_time(mtime)}</td>"
                        f'<td><a href="/web/imports/file/{d_index}/{html_escape(f.name)}">{html_escape(f.name)}</a></td></tr>'
                    )

        if allowed:
            dirs_note = "Log files are read from: " + ", ".join(f"<code>{html_escape(str(d))}</code>" for d in allowed)
        else:
            dirs_note = ("No log directory is configured (<code>KB_IMPORT_LOG_DIRS</code>), "
                         "so runs are listed without their logs.")

        headers = ["Started", "Status", "Source", "Host", "Trigger", "Duration", "Found",
                   "New docs", "Skipped", "Failed", "Summarized", "Embedded", "Errors", "Error", "Log"]
        table = (
            '<div style="overflow-x:auto;"><table class="data-table" style="width:100%; border-collapse:collapse; font-size:13px;">'
            "<thead><tr>" + "".join(f'<th style="text-align:left; padding:4px 8px;">{h}</th>' for h in headers) + "</tr></thead>"
            "<tbody>" + ("".join(rows) or f'<tr><td colspan="{len(headers)}">No import runs recorded.</td></tr>') + "</tbody></table></div>"
        )
        orphan_html = ""
        if orphans:
            orphan_html = (
                '<div class="card"><h2>Log files without a run record</h2>'
                "<p>Runs that stopped before the import itself started (credentials, environment, configuration).</p>"
                '<table style="font-size:13px;"><thead><tr><th style="text-align:left;">Modified</th><th style="text-align:left;">Log</th></tr></thead>'
                "<tbody>" + "".join(orphans[:100]) + "</tbody></table></div>"
            )

        content = f"""
        {session_manager.get_auth_warning_html()}
        <div class="card">
            <h1>Import Runs</h1>
            <p>Runs of the scheduled import job, newest first. {dirs_note}</p>
        </div>
        <div class="card">{table}</div>
        {orphan_html}
        <style>.data-table td {{ padding: 4px 8px; border-top: 1px solid #eee; white-space: nowrap; }}</style>
        """
        return HTMLResponse(html_templates.base_template("Import Runs", content, None, username))

    async def web_import_run_log(request: Request):
        _, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        # The path comes from the run's database row, never from the request.
        runs = get_import_runs(run_id=request.path_params["run_id"], limit=2)
        if len(runs) != 1:
            return PlainTextResponse("No such import run.", status_code=404)
        log_file = allowed_log_path(runs[0].get("log_path"), get_import_log_dirs())
        if not log_file:
            return PlainTextResponse("This run's log file is not available to the web server.", status_code=404)
        return PlainTextResponse(read_log_tail(log_file))

    async def web_import_log_file(request: Request):
        _, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        allowed = get_import_log_dirs()
        name = request.path_params["name"]
        try:
            d = allowed[int(request.path_params["dir_index"])]
        except (ValueError, IndexError):
            return PlainTextResponse("Not found.", status_code=404)
        if not LOG_NAME_RE.match(name):
            return PlainTextResponse("Not found.", status_code=404)
        log_file = allowed_log_path(str(d / name), allowed)
        if not log_file:
            return PlainTextResponse("Not found.", status_code=404)
        return PlainTextResponse(read_log_tail(log_file))

    app.add_route("/web/imports", web_imports)
    app.add_route("/web/imports/{run_id}/log", web_import_run_log)
    app.add_route("/web/imports/file/{dir_index}/{name}", web_import_log_file)
