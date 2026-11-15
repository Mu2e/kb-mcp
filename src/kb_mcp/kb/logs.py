"""Unified logging functions for all operation logs (search, parsing, chunking)."""

from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .database import get_db_session
from .search.db_models import SearchLog


def _to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Convert datetime to UTC and return ISO format string with 'Z' suffix.
    
    Simple approach: ensure datetime is UTC, then return ISO format with 'Z' suffix.
    JavaScript will parse 'Z' as UTC and convert to local time.
    """
    if dt is None:
        return None
    
    # Convert to UTC if needed
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    
    # Get ISO format string - remove microseconds for cleaner format
    iso_str = dt.strftime('%Y-%m-%dT%H:%M:%S')
    
    # Always append 'Z' to indicate UTC
    return iso_str + 'Z'


from .embedding.db_models import ParsingLog, ChunkEmbeddingLog, SummaryLog


def get_search_logs(
    limit: int = 10,
    offset: int = 0,
    query: Optional[str] = None,
    embedding_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_time_search_total: Optional[float] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get search logs, optionally filtered.
    
    Args:
        limit: Maximum number of logs to return (default: 10)
        offset: Number of logs to skip (default: 0)
        query: Optional query text to filter by (partial match)
        embedding_name: Optional embedding name to filter by
        date_from: Optional start date (ISO format string or date string)
        date_to: Optional end date (ISO format string or date string)
        min_time_search_total: Optional minimum total search time in seconds
        session: Optional database session. If None, creates a new session.
    
    Returns:
        List of search log dictionaries
    """

    with get_db_session(session) as session:
        return _get_search_logs_impl(session, limit, offset, query, embedding_name, date_from, date_to, min_time_search_total)


def _get_search_logs_impl(
    session: Session,
    limit: int,
    offset: int,
    query: Optional[str],
    embedding_name: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    min_time_search_total: Optional[float],
) -> List[Dict[str, Any]]:
    """Internal implementation of get_search_logs."""
    from datetime import timedelta
    
    query_obj = session.query(SearchLog)
    
    # Apply filters
    if query:
        query_obj = query_obj.filter(SearchLog.query.contains(query))
    if embedding_name:
        query_obj = query_obj.filter(SearchLog.embedding_name == embedding_name)
    
    # Date range filters
    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            query_obj = query_obj.filter(SearchLog.created_time >= date_from_dt)
        except (ValueError, AttributeError):
            # Try parsing as simple date string
            try:
                date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
                query_obj = query_obj.filter(SearchLog.created_time >= date_from_dt)
            except ValueError:
                pass  # Skip invalid date
    
    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            # Add one day to include the entire end date
            date_to_dt = date_to_dt + timedelta(days=1)
            query_obj = query_obj.filter(SearchLog.created_time < date_to_dt)
        except (ValueError, AttributeError):
            # Try parsing as simple date string
            try:
                date_to_dt = datetime.strptime(date_to, '%Y-%m-%d')
                date_to_dt = date_to_dt + timedelta(days=1)
                query_obj = query_obj.filter(SearchLog.created_time < date_to_dt)
            except ValueError:
                pass  # Skip invalid date
    
    # Minimum search time filter
    if min_time_search_total is not None:
        query_obj = query_obj.filter(SearchLog.time_search_total >= min_time_search_total)
    
    # Order by most recent first
    query_obj = query_obj.order_by(desc(SearchLog.created_time))
    
    # Apply limit and offset
    logs = query_obj.limit(limit).offset(offset).all()
    
    # Convert to dictionaries
    return [log.to_dict() for log in logs]


def get_parsing_logs(
    document_id: str,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get parsing logs for a document.
    
    Args:
        document_id: Document ID to get logs for
        limit: Optional maximum number of logs to return
        session: Optional database session. If None, creates a new session.
    
    Returns:
        List of parsing log dictionaries
    """
    with get_db_session(session) as session:
        return _get_parsing_logs_impl(session, document_id, limit)


def _get_parsing_logs_impl(
    session: Session,
    document_id: str,
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    """Internal implementation of get_parsing_logs."""
    query = session.query(ParsingLog).filter(
        ParsingLog.document_id == document_id
    ).order_by(desc(ParsingLog.insertion_time))
    
    if limit:
        query = query.limit(limit)
    
    logs = query.all()
    
    # Convert to dictionaries
    result = []
    for log in logs:
        # Get file_path and source_type from document if available
        file_path = None
        source_type = None
        if log.document:
            source_type = log.document.source_type
            if log.document.uri:
                file_path = log.document.uri
            elif log.document.meta and isinstance(log.document.meta, dict):
                file_path = log.document.meta.get("file_path")
        
        result.append({
            "id": log.id,
            "document_id": log.document_id,
            "insertion_time": _to_utc_iso(log.insertion_time),
            "text_extraction_time_seconds": log.text_extraction_time_seconds,
            "image_description_time_seconds": log.image_description_time_seconds,
            "total_time_seconds": log.total_time_seconds,
            "file_path": file_path,
            "source_type": source_type,
            "num_documents": log.num_documents,
            "text_length": log.text_length,
            "hostname": log.hostname,
            "meta": log.meta if log.meta else {},
        })
    
    return result


def get_chunking_logs(
    document_id: str,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get chunking/embedding logs for a document.
    
    Args:
        document_id: Document ID to get logs for
        limit: Optional maximum number of logs to return
        session: Optional database session. If None, creates a new session.
    
    Returns:
        List of chunking log dictionaries
    """
    with get_db_session(session) as session:
        return _get_chunking_logs_impl(session, document_id, limit)


def _get_chunking_logs_impl(
    session: Session,
    document_id: str,
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    """Internal implementation of get_chunking_logs."""
    query = session.query(ChunkEmbeddingLog).filter(
        ChunkEmbeddingLog.document_id == document_id
    ).order_by(desc(ChunkEmbeddingLog.insertion_time))
    
    if limit:
        query = query.limit(limit)
    
    logs = query.all()
    
    # Convert to dictionaries
    return [{
        "id": log.id,
        "document_id": log.document_id,
        "insertion_time": _to_utc_iso(log.insertion_time),
        "chunking_time_seconds": log.chunking_time_seconds,
        "embedding_time_seconds": log.embedding_time_seconds,
        "total_time_seconds": log.total_time_seconds,
        "num_chunks": log.num_chunks,
        "num_embeddings": log.num_embeddings,
        "chunk_strategy": log.chunk_strategy,
        "embedding_name": log.embedding_name,
        "hostname": log.hostname,
        "meta": log.meta if log.meta else {},
    } for log in logs]


def get_summary_logs(
    document_id: str,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get summary generation logs for a document.

    Args:
        document_id: Document ID to get logs for
        limit: Optional maximum number of logs to return
        session: Optional database session. If None, creates a new session.

    Returns:
        List of summary log dictionaries
    """
    with get_db_session(session) as session:
        return _get_summary_logs_impl(session, document_id, limit)


def _get_summary_logs_impl(
    session: Session,
    document_id: str,
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    """Internal implementation of get_summary_logs."""
    query = session.query(SummaryLog).filter(
        SummaryLog.document_id == document_id
    ).order_by(desc(SummaryLog.insertion_time))

    if limit:
        query = query.limit(limit)

    logs = query.all()

    # Convert to dictionaries
    return [{
        "id": log.id,
        "document_id": log.document_id,
        "insertion_time": _to_utc_iso(log.insertion_time),
        "model": log.model,
        "time_summary": log.time_summary,
        "hostname": log.hostname,
        "meta": log.meta if log.meta else {},
    } for log in logs]


def get_all_logs_for_document(
    document_id: str,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Get all logs (parsing, chunking, summary) for a document.

    Args:
        document_id: Document ID to get logs for
        limit: Optional maximum number of logs per type to return
        session: Optional database session. If None, creates a new session.

    Returns:
        Dictionary with keys: "parsing", "chunking", "summary", "search"
        Each contains a list of log dictionaries
    """
    with get_db_session(session) as session:
        return {
            "parsing": get_parsing_logs(document_id, limit=limit, session=session),
            "chunking": get_chunking_logs(document_id, limit=limit, session=session),
            "summary": get_summary_logs(document_id, limit=limit, session=session),
            "search": [],  # Search logs are not per-document
        }



def get_import_runs(
    source_id: Optional[str] = None,
    limit: Optional[int] = 20,
    run_id: Optional[str] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get import-run records (one per `Source.process_all()` run), newest first.

    Args:
        source_id: Optional source to restrict to (e.g. "mu2e-docdb")
        limit: Optional maximum number of runs to return
        run_id: Optional run ID (or unique prefix of one) to fetch just that run
        session: Optional database session. If None, creates a new session.

    Returns:
        List of import-run dictionaries
    """
    with get_db_session(session) as session:
        return _get_import_runs_impl(session, source_id, limit, run_id)


def _get_import_runs_impl(
    session: Session,
    source_id: Optional[str],
    limit: Optional[int],
    run_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Internal implementation of get_import_runs."""
    from .db_models import ImportRun

    query = session.query(ImportRun)
    if source_id:
        query = query.filter(ImportRun.source_id == source_id)
    if run_id:
        query = query.filter(ImportRun.id.startswith(run_id))
    query = query.order_by(desc(ImportRun.started_time))
    if limit:
        query = query.limit(limit)

    columns = [c.name for c in ImportRun.__table__.columns]
    result = []
    for run in query.all():
        row = {name: getattr(run, name) for name in columns}
        row["started_time"] = _to_utc_iso(run.started_time)
        row["finished_time"] = _to_utc_iso(run.finished_time)
        row["duration_seconds"] = (
            (run.finished_time - run.started_time).total_seconds()
            if run.finished_time and run.started_time else None
        )
        result.append(row)
    _attach_privacy_runs(session, result)
    return result


def _attach_privacy_runs(session: Session, runs: List[Dict[str, Any]]) -> None:
    """Add each import run's privacy step (scan + policy) as run["privacy"], or None.

    The import job runs `kb tools visibility scan` and `apply-policy` with
    KB_RUN_LOG set to its own log file, so their DocumentVisibilityRun rows
    share the import run's log_path; that is the join.
    """
    from .db_models import DocumentVisibilityRun

    paths = {r["log_path"] for r in runs if r.get("log_path")}
    by_path: Dict[str, Dict[str, Any]] = {}
    if paths:
        rows = (
            session.query(DocumentVisibilityRun)
            .filter(DocumentVisibilityRun.log_path.in_(list(paths)))
            .order_by(DocumentVisibilityRun.started_time)
            .all()
        )
        for v in rows:
            kind = (v.meta or {}).get("kind")
            p = by_path.setdefault(v.log_path, {})
            if kind == "sensitivity-scan":
                p["scanned"] = p.get("scanned", 0) + (v.documents_scanned or 0)
                p["scan_errors"] = p.get("scan_errors", 0) + (v.errors or 0)
            elif kind == "privacy-policy":
                meta = v.meta or {}
                p["hidden"] = p.get("hidden", 0) + (v.newly_hidden or 0)
                p["lifted"] = p.get("lifted", 0) + (v.newly_visible or 0)
                p["redactions"] = p.get("redactions", 0) + (meta.get("redactions_added") or 0)
                p["flagged"] = meta.get("flagged")
                p["held"] = meta.get("held")  # still held after this run
    for r in runs:
        r["privacy"] = by_path.get(r.get("log_path")) if r.get("log_path") else None


def get_privacy_hold_summary(stuck_hours: float = 24.0, session: Optional[Session] = None) -> Dict[str, int]:
    """Documents currently on the privacy hold, and how many have waited longer than `stuck_hours`.

    A hold normally lasts until the end of the import run that created it;
    one that outlives a day means the privacy step is not running or keeps
    failing for that document.
    """
    from datetime import timedelta
    from .db_models import DocumentVisibilityCurrent, DocumentVisibilityDecision

    with get_db_session(session) as session:
        rows = (
            session.query(DocumentVisibilityCurrent.updated_time)
            .join(DocumentVisibilityDecision, DocumentVisibilityDecision.id == DocumentVisibilityCurrent.decision_id)
            .filter(
                DocumentVisibilityDecision.meta["origin"].astext == "privacy-hold",
                DocumentVisibilityCurrent.visible_get.is_(False),
            )
            .all()
        )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=stuck_hours)
    stuck = sum(1 for (t,) in rows if t is not None and (t if t.tzinfo else t.replace(tzinfo=timezone.utc)) < cutoff)
    return {"held": len(rows), "stuck": stuck}


def get_mcp_usage(days: Optional[float] = 30, session: Optional[Session] = None) -> Dict[str, Any]:
    """MCP tool usage from logs_mcp_calls over the last `days` (None: all time).

    Aggregated in Python (one small row per call), so it runs on SQLite too.
    Returns {"since", "tools": [{"tool", "calls", "sessions", "errors",
    "median_ms", "p95_ms", "last_used"}], "sessions": [{"session_id",
    "first", "last", "calls", "tools": {tool: count}}], "totals": {...}}.
    """
    from datetime import timedelta
    from .db_models import McpCallLog

    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    with get_db_session(session) as session:
        query = session.query(
            McpCallLog.tool, McpCallLog.session_id, McpCallLog.transport,
            McpCallLog.status, McpCallLog.duration_ms, McpCallLog.created_time,
        )
        if since is not None:
            query = query.filter(McpCallLog.created_time >= since)
        rows = query.all()

    def percentile(values, q):
        if not values:
            return None
        values = sorted(values)
        return round(values[min(len(values) - 1, int(q * len(values)))], 1)

    tools: Dict[str, Dict[str, Any]] = {}
    sessions: Dict[str, Dict[str, Any]] = {}
    transports: Dict[str, int] = {}
    for tool, sid, transport, status, duration, created in rows:
        t = tools.setdefault(tool, {"tool": tool, "calls": 0, "sessions": set(), "errors": 0,
                                    "durations": [], "last_used": created})
        t["calls"] += 1
        t["errors"] += status == "error"
        if sid:
            t["sessions"].add(sid)
        if duration is not None:
            t["durations"].append(duration)
        t["last_used"] = max(t["last_used"], created)
        transports[transport or "unknown"] = transports.get(transport or "unknown", 0) + 1
        if sid:
            s_ = sessions.setdefault(sid, {"session_id": sid, "first": created, "last": created,
                                           "calls": 0, "tools": {}})
            s_["calls"] += 1
            s_["tools"][tool] = s_["tools"].get(tool, 0) + 1
            s_["first"], s_["last"] = min(s_["first"], created), max(s_["last"], created)

    tool_rows = sorted(
        ({"tool": t["tool"], "calls": t["calls"], "sessions": len(t["sessions"]), "errors": t["errors"],
          "median_ms": percentile(t["durations"], 0.5), "p95_ms": percentile(t["durations"], 0.95),
          "last_used": _to_utc_iso(t["last_used"])} for t in tools.values()),
        key=lambda t: -t["calls"],
    )
    session_rows = sorted(sessions.values(), key=lambda s_: s_["last"], reverse=True)
    for s_ in session_rows:
        s_["first"], s_["last"] = _to_utc_iso(s_["first"]), _to_utc_iso(s_["last"])
    return {
        "since": _to_utc_iso(since) if since else None,
        "tools": tool_rows,
        "sessions": session_rows,
        "totals": {"calls": len(rows), "sessions": len(session_rows), "by_transport": transports},
    }


USAGE_TIMEZONE = "America/Chicago"


def get_mcp_usage_daily(days: int = 30, tz: str = USAGE_TIMEZONE,
                        session: Optional[Session] = None) -> Dict[str, Any]:
    """MCP calls per day and tool, and distinct sessions per day, for a public page.

    Counts only: no session identifiers, queries, users or error details
    leave this function, so the result can be shown without logging in.
    Days are calendar days in `tz` (Fermilab time by default), the last
    `days` of them including today, with days without calls present as
    zeros so a chart has no gaps. Aggregated in Python like get_mcp_usage()
    (one small row per call; ~1,600 rows a month in 2026), so it runs on
    SQLite too.

    Returns {"days", "timezone", "tools": [names, busiest first],
    "series": [{"date": "YYYY-MM-DD", "calls": {tool: n}, "total", "sessions",
    "errors"}], "totals": {"calls", "sessions", "errors", "by_tool": {tool: n}},
    "latency_ms": {tool: {"median", "p95"}}}.
    """
    from datetime import timedelta
    from .db_models import McpCallLog

    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001 - no tz database: fall back to UTC
        zone, tz = timezone.utc, "UTC"
    days = max(1, min(int(days), 366))
    today = datetime.now(zone).date()
    first_day = today - timedelta(days=days - 1)
    start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=zone)

    with get_db_session(session) as session:
        rows = (
            session.query(McpCallLog.tool, McpCallLog.session_id, McpCallLog.status,
                          McpCallLog.duration_ms, McpCallLog.created_time)
            .filter(McpCallLog.created_time >= start.astimezone(timezone.utc))
            .all()
        )

    per_day: Dict[str, Dict[str, Any]] = {}
    for i in range(days):
        d = (first_day + timedelta(days=i)).isoformat()
        per_day[d] = {"date": d, "calls": {}, "total": 0, "sessions": set(), "errors": 0}
    all_sessions, by_tool, durations, errors = set(), {}, {}, 0
    for tool, sid, status, duration, created in rows:
        if created.tzinfo is None:  # SQLite returns naive UTC
            created = created.replace(tzinfo=timezone.utc)
        day = per_day.get(created.astimezone(zone).date().isoformat())
        if day is None:
            continue
        day["calls"][tool] = day["calls"].get(tool, 0) + 1
        day["total"] += 1
        if sid:
            day["sessions"].add(sid)
            all_sessions.add(sid)
        if status == "error":
            day["errors"] += 1
            errors += 1
        by_tool[tool] = by_tool.get(tool, 0) + 1
        if duration is not None:
            durations.setdefault(tool, []).append(duration)

    def percentile(values, q):
        values = sorted(values)
        return round(values[min(len(values) - 1, int(q * len(values)))]) if values else None

    series = []
    for day in per_day.values():
        day["sessions"] = len(day["sessions"])
        series.append(day)
    return {
        "days": days,
        "timezone": tz,
        "tools": sorted(by_tool, key=lambda t: -by_tool[t]),
        "series": series,
        "totals": {"calls": sum(by_tool.values()), "sessions": len(all_sessions), "errors": errors,
                   "by_tool": by_tool},
        "latency_ms": {t: {"median": percentile(v, 0.5), "p95": percentile(v, 0.95)} for t, v in durations.items()},
    }
