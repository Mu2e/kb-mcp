"""Web interface routes for evaluation module."""

import json
import logging
from datetime import datetime, timezone
from html import escape as html_escape
from starlette.responses import HTMLResponse
from starlette.requests import Request
from sqlalchemy.orm import joinedload

from ..auth import WebSessionManager
from .. import html_templates

logger = logging.getLogger(__name__)


def _to_utc_iso(dt: datetime | None) -> str | None:
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


def _render_agentic_conversation(conversation: list) -> str:
    """Render the agentic conversation (tool calls + results) as collapsible HTML."""
    if not conversation:
        return ""

    role_colors = {"system": "#f5f5f5", "user": "#e8f4e8", "assistant": "#e8f0ff", "tool": "#fff8e1"}
    role_labels = {"system": "System", "user": "User", "assistant": "Assistant", "tool": "Tool Result"}

    turns_html = ""
    for i, msg in enumerate(conversation):
        role = msg.get("role", "unknown")
        color = role_colors.get(role, "#fafafa")
        label = role_labels.get(role, role.title())

        # Build content display
        content_parts = []
        if msg.get("content"):
            content_parts.append(html_escape(str(msg["content"])))
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                content_parts.append(f'<em>Tool call:</em> <code>{html_escape(fn.get("name", "?"))}({html_escape(fn.get("arguments", ""))})</code>')

        content_html = "<br>".join(content_parts) if content_parts else "<em>(empty)</em>"

        turns_html += f"""
        <div style="margin-bottom: 6px; padding: 8px 12px; background: {color}; border-radius: 4px; border-left: 3px solid #ccc;">
            <div style="font-size: 11px; font-weight: 600; color: #666; margin-bottom: 4px; text-transform: uppercase;">{label}</div>
            <div style="font-size: 13px; white-space: pre-wrap; word-break: break-word;">{content_html}</div>
        </div>"""

    unique_id = f"agentic-conv-{id(conversation)}"
    return f"""
    <div style="margin-top: 15px;">
        <strong>Agentic Conversation</strong>
        <span onclick="document.getElementById('{unique_id}').style.display = document.getElementById('{unique_id}').style.display === 'none' ? 'block' : 'none'"
              style="margin-left: 8px; cursor: pointer; color: #2196F3; font-size: 13px;">[toggle]</span>
        <div id="{unique_id}" style="display: none; margin-top: 8px; border: 1px solid #ddd; border-radius: 4px; padding: 8px; max-height: 600px; overflow-y: auto;">
            {turns_html}
        </div>
    </div>"""


_TH = 'style="text-align: {align}; padding: 8px 10px; font-weight: 600; white-space: nowrap;"'
_TD = 'style="text-align: {align}; padding: 8px 10px; white-space: nowrap;{extra}"'


def _rate_cell(n, d) -> str:
    """"n/d" with a background shade by rate, so strong and weak runs stand out."""
    if n is None or not d:
        return f'<td {_TD.format(align="right", extra=" color: #999;")}>—</td>'
    rate = n / d
    shade = "#e8f5e9" if rate >= 0.8 else "#fff8e1" if rate >= 0.5 else "#ffebee"
    return (f'<td {_TD.format(align="right", extra=f" background-color: {shade};")} '
            f'title="{rate:.0%}">{n}/{d}</td>')


def _plain_cell(value, fmt="{}") -> str:
    text = fmt.format(value) if value is not None else "—"
    color = " color: #999;" if value is None else ""
    return f'<td {_TD.format(align="right", extra=color)}>{html_escape(text)}</td>'


def _short_run_name(name, prefix) -> str:
    name = name or ""
    if prefix and name.startswith(prefix):
        name = name[len(prefix):].lstrip("-") or name
    return name


def _render_compare_table(rows, prefix) -> str:
    """Summary table of compare_runs() rows."""
    head = "".join(
        f'<th {_TH.format(align=a)}>{h}</th>' for h, a in (
            ("Run", "left"), ("Type", "left"), ("Answer model", "left"), ("Judge", "left"), ("Q", "right"),
            ("Exact", "right"), ("Entry", "right"), ("Judge ✓", "right"), ("Saw", "right"), ("Opened", "right"),
            ("Limit", "right"), ("Stop", "right"), ("Ans s", "right"), ("Ans tokens", "right"), ("Judge tokens", "right"),
        )
    )
    body = ""
    for r in rows:
        n = r["questions"]
        body += (
            '<tr style="border-bottom: 1px solid #eee;">'
            f'<td {_TD.format(align="left", extra="")}><a href="/web/eval/run/{r["run_id"]}" '
            f'style="color: #2196F3; text-decoration: none;">{html_escape(_short_run_name(r["name"], prefix))}</a></td>'
            f'<td {_TD.format(align="left", extra=" color: #666;")}>{html_escape(r["search_type"] or "—")}</td>'
            f'<td {_TD.format(align="left", extra=" color: #666;")}>{html_escape(r["answer_model"] or "—")}</td>'
            f'<td {_TD.format(align="left", extra=" color: #666;")}>{html_escape(r["judge_model"] or "—")}</td>'
            + _plain_cell(n)
            + _rate_cell(r["exact_hits"], n)
            + _rate_cell(r["entry_hits"], n)
            + _rate_cell(r["judge_correct"], r["judged"])
            + _plain_cell(r["agent_saw_source"]) + _plain_cell(r["agent_opened_source"])
            + _plain_cell(r["turn_limit"]) + _plain_cell(r["stopped"])
            + _plain_cell(r["avg_answer_seconds"], "{:.0f}")
            + _plain_cell(r["answer_tokens"], "{:,}") + _plain_cell(r["judge_tokens"], "{:,}")
            + "</tr>"
        )
    return f"""
    <div style="overflow-x: auto;">
    <table style="width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px;">
        <thead><tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">{head}</tr></thead>
        <tbody>{body}</tbody>
    </table>
    </div>
    <p style="color: #666; font-size: 12px; margin-top: 10px; line-height: 1.5;">
        <b>Exact</b>: source row retrieved; <b>Entry</b>: any file or figure of the source's DocDB entry retrieved;
        <b>Judge ✓</b>: answers the judge accepted; <b>Saw / Opened</b>: the agent saw the source in a tool result /
        opened it with kb_get; <b>Limit / Stop</b>: the agent hit the turn limit / the endpoint stopped it (e.g. context
        overflow). For agentic and llm_only runs, Exact and Entry come from a separate search the agent never sees.
        Shading: green ≥ 80%, yellow ≥ 50%, red below.
    </p>
    """


def _render_question_matrix(block, prefix) -> str:
    """Question-by-run verdict grid for one question set."""
    runs = block["runs"]
    head = f'<th {_TH.format(align="left")}>Question</th>' + "".join(
        f'<th {_TH.format(align="center")} title="{html_escape(r["name"] or "")}">'
        f'<a href="/web/eval/run/{r["run_id"]}" style="color: #2196F3; text-decoration: none;">{i}</a></th>'
        for i, r in enumerate(runs, 1)
    )
    body = ""
    for row in block["rows"]:
        cells = ""
        for cell in row["cells"]:
            if cell is None:
                cells += f'<td {_TD.format(align="center", extra=" color: #ccc;")}>·</td>'
                continue
            mark, color = ("✓", "#2e7d32") if cell["ok"] else ("✗", "#c62828")
            cells += (f'<td {_TD.format(align="center", extra="")}><a href="/web/eval/result/{cell["result_id"]}" '
                      f'style="color: {color}; text-decoration: none; font-weight: 600;">{mark}</a></td>')
        body += (
            '<tr style="border-bottom: 1px solid #eee;">'
            f'<td style="padding: 8px 10px; max-width: 640px;"><a href="/web/eval/question/{row["question_id"]}" '
            f'style="color: #333; text-decoration: none;">{html_escape(row["question"])}</a></td>{cells}</tr>'
        )
    legend = "".join(
        f'<li><b>{i}</b> {html_escape(_short_run_name(r["name"], prefix))}</li>' for i, r in enumerate(runs, 1)
    )
    return f"""
    <h3 style="margin-top: 24px;">Questions from
        <a href="/web/eval/generation/{block['generation_id']}" style="color: #2196F3; text-decoration: none;">{html_escape(block['generation_name'])}</a>
    </h3>
    <p style="color: #666; font-size: 12px;">Hardest questions first. A cell is the judge's verdict, or for unjudged
        retrieval runs whether the source's entry was retrieved; click it for the answer.</p>
    <div style="overflow-x: auto;">
    <table style="border-collapse: collapse; font-size: 13px;">
        <thead><tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">{head}</tr></thead>
        <tbody>{body}</tbody>
    </table>
    </div>
    <ol style="list-style: none; padding-left: 0; color: #666; font-size: 12px; columns: 2;">{legend}</ol>
    """


def setup_eval_routes(app, session_manager: WebSessionManager, require_auth_html):
    """Register evaluation web routes.

    Args:
        app: Starlette application instance
        session_manager: WebSessionManager instance for authentication
        require_auth_html: Authentication helper function
    """
    
    async def web_eval_overview(request: Request):
        """Eval overview page showing list of EvalGenerations and EvalRuns."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        
        # Get username from authenticated session
        username = session_data.get("username")

        # Get all eval generations and runs
        from ....kb.eval.db_models import get_eval_generation, get_eval_run
        from ....kb.database import get_db_session_ephemeral
        
        with get_db_session_ephemeral() as session:
            generations = get_eval_generation(limit=100, session=session) or []
            runs = get_eval_run(limit=100, session=session) or []
            
            # Build generations list (while still in session)
            if generations:
                generations_html = """
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <thead>
                    <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                        <th style="text-align: left; padding: 10px; font-weight: 600;">ID</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Type</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Method</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Model</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Questions</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Source</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Created</th>
                    </tr>
                </thead>
                <tbody>
                """
                for gen in generations:
                    num_questions = len(gen.questions) if gen.questions else 0
                    created_time_iso = _to_utc_iso(gen.created_time) if gen.created_time else None
                    method_display = gen.generation_method or "—"
                    source_display = gen.source_id or "—"
                    # Get model from meta
                    model_display = (gen.meta.get("model") if gen.meta else None) or "—"
                    
                    generations_html += f"""
                    <tr style="border-bottom: 1px solid #eee; cursor: pointer; transition: background-color 0.15s;" 
                        onclick="window.location.href='/web/eval/generation/{gen.id}'"
                        onmouseover="this.style.backgroundColor='#f8f9fa'"
                        onmouseout="this.style.backgroundColor='transparent'">
                        <td style="padding: 12px 10px;">
                            <a href="/web/eval/generation/{gen.id}" style="text-decoration: none; color: #2196F3; font-weight: 500;">
                                <code style="font-size: 11px;">{gen.id[:8]}...</code>
                            </a>
                        </td>
                        <td style="padding: 12px 10px; color: #666;">{html_escape(gen.generation_type)}</td>
                        <td style="padding: 12px 10px; color: #666;">{html_escape(method_display)}</td>
                        <td style="padding: 12px 10px; color: #666;">{html_escape(model_display)}</td>
                        <td style="padding: 12px 10px; color: #666;">{num_questions}</td>
                        <td style="padding: 12px 10px; color: #666;">{html_escape(source_display)}</td>
                        <td style="padding: 12px 10px; color: #666;">
                            <span class="utc-timestamp" data-iso="{created_time_iso or ''}">{created_time_iso or 'N/A'}</span>
                        </td>
                    </tr>
                    """
                generations_html += """
                </tbody>
            </table>
                """
            else:
                generations_html = '<div class="info-box">No eval datasets found.</div>'

            # Build runs list (while still in session)
            if runs:
                runs_html = """
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <thead>
                    <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Name</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Embedding</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Dataset</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Max Results</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Created</th>
                    </tr>
                </thead>
                <tbody>
                """
                for run in runs:
                    created_time_iso = _to_utc_iso(run.created_time) if run.created_time else None
                    name_display = run.name or f"Run {run.id[:8]}"
                    embedding_display = run.embedding_name or "—"
                    generation_link = ""
                    if run.generation_id:
                        # Show generation ID in same format as Eval Datasets table
                        generation_link = f'<a href="/web/eval/generation/{run.generation_id}" style="text-decoration: none; color: #2196F3; font-weight: 500;"><code style="font-size: 11px;">{run.generation_id[:8]}...</code></a>'
                    else:
                        generation_link = "—"
                    max_results_display = run.max_results or "—"
                    
                    runs_html += f"""
                    <tr style="border-bottom: 1px solid #eee; cursor: pointer; transition: background-color 0.15s;" 
                        onclick="window.location.href='/web/eval/run/{run.id}'"
                        onmouseover="this.style.backgroundColor='#f8f9fa'"
                        onmouseout="this.style.backgroundColor='transparent'">
                        <td style="padding: 12px 10px;">
                            <a href="/web/eval/run/{run.id}" style="text-decoration: none; color: #2196F3; font-weight: 500;">
                                {html_escape(name_display)}
                            </a>
                        </td>
                        <td style="padding: 12px 10px; color: #666;">{html_escape(embedding_display)}</td>
                        <td style="padding: 12px 10px; color: #666;">{generation_link}</td>
                        <td style="padding: 12px 10px; color: #666;">{max_results_display}</td>
                        <td style="padding: 12px 10px; color: #666;">
                            <span class="utc-timestamp" data-iso="{created_time_iso or ''}">{created_time_iso or 'N/A'}</span>
                        </td>
                    </tr>
                    """
                runs_html += """
                </tbody>
            </table>
                """
            else:
                runs_html = '<div class="info-box">No evaluation runs found.</div>'

        content = f"""
        <h1>Evaluation Overview</h1>
        <p><a href="/web/eval/compare" style="color: #2196F3; text-decoration: none; font-weight: 500;">Compare runs →</a>
            <span style="color: #666;">side-by-side results of a grid, with a per-question view</span></p>
        
        <div class="card">
            <h2>Eval Datasets ({len(generations) if generations else 0})</h2>
            {generations_html}
        </div>
        
        <div class="card" style="margin-top: 20px;">
            <h2>Evaluation Runs ({len(runs) if runs else 0})</h2>
            {runs_html}
        </div>
        <script>
        // Format UTC timestamps on page load
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.utc-timestamp').forEach(function(element) {{
                const isoString = element.getAttribute('data-iso');
                if (isoString) {{
                    element.textContent = formatLocalTime(isoString);
                }}
            }});
        }});
        </script>
        """

        return HTMLResponse(html_templates.base_template(
            "Evaluation Overview - MCP Server",
            content,
            None,
            username
        ))
    app.add_route("/web/eval", web_eval_overview)

    async def web_eval_compare(request: Request):
        """Compare runs side by side: summary table plus a question-by-run matrix.

        Select runs with ?prefix=<run name prefix> (one grid) and/or repeated
        ?run=<run id>. Without either, lists the run groups to pick from.
        """
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        username = session_data.get("username")

        from starlette.concurrency import run_in_threadpool
        from ....kb.eval.grid import compare_runs, per_question_matrix, run_groups

        prefix = (request.query_params.get("prefix") or "").strip() or None
        run_ids = request.query_params.getlist("run") or None

        def _load():
            groups = run_groups()
            if not prefix and not run_ids:
                return groups, [], []
            return groups, compare_runs(name_prefix=prefix, run_ids=run_ids), per_question_matrix(
                name_prefix=prefix, run_ids=run_ids)

        # Off the event loop: a large grid's comparison is several queries.
        groups, rows, blocks = await run_in_threadpool(_load)

        group_links = " ".join(
            f'<a href="/web/eval/compare?prefix={html_escape(g["prefix"])}-" '
            f'style="display: inline-block; margin: 3px 6px 3px 0; padding: 4px 10px; border-radius: 12px; '
            f'text-decoration: none; font-size: 13px; '
            f'{"background-color: #2196F3; color: white;" if prefix and prefix.rstrip("-") == g["prefix"] else "background-color: #eef4fb; color: #1565c0;"}">'
            f'{html_escape(g["prefix"])} <span style="opacity: 0.7;">({g["runs"]})</span></a>'
            for g in groups
        ) or '<span style="color: #999;">No grid runs yet.</span>'

        if rows:
            results_html = f"""
            <div class="card" style="margin-top: 20px;">
                <h2>Runs ({len(rows)})</h2>
                {_render_compare_table(rows, prefix)}
            </div>
            <div class="card" style="margin-top: 20px;">
                <h2>Per question</h2>
                {"".join(_render_question_matrix(b, prefix) for b in blocks) or '<div class="info-box">No results.</div>'}
            </div>
            """
        elif prefix or run_ids:
            results_html = '<div class="info-box" style="margin-top: 20px;">No runs match.</div>'
        else:
            results_html = '<div class="info-box" style="margin-top: 20px;">Pick a run group above.</div>'

        content = f"""
        <h1>Compare Evaluation Runs</h1>
        <p><a href="/web/eval" style="color: #2196F3; text-decoration: none;">← Evaluation overview</a></p>
        <div class="card">
            <h2>Run groups</h2>
            <div>{group_links}</div>
            <p style="color: #666; font-size: 12px; margin-top: 8px;">A group is every run whose name starts with the
                prefix, e.g. one <code>kb eval grid --prefix …</code>.</p>
        </div>
        {results_html}
        """
        return HTMLResponse(html_templates.base_template(
            "Compare Evaluation Runs - MCP Server",
            content,
            None,
            username
        ))
    app.add_route("/web/eval/compare", web_eval_compare)

    async def web_eval_generation(request: Request):
        """Eval generation detail page showing questions list."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        
        # Get username from authenticated session
        username = session_data.get("username")

        generation_id = request.path_params["generation_id"]

        # Get generation and questions
        from ....kb.eval.db_models import get_eval_generation, get_eval_questions, get_eval_run
        from ....kb.database import get_db_session_ephemeral
        
        with get_db_session_ephemeral() as session:
            generation = get_eval_generation(generation_id=generation_id, session=session)
            
            if not generation:
                return HTMLResponse(
                    html_templates.base_template(
                        "Generation Not Found",
                        '<div class="error-box"><h2>Dataset Not Found</h2><p>The requested eval dataset could not be found.</p><p><a href="/web/eval">← Back to Evaluation Overview</a></p></div>',
                        None,
                        username
                    ),
                    status_code=404
                )

            # Get questions for this generation
            questions = get_eval_questions(generation_id=generation_id, session=session) or []
            
            # Get runs for this generation
            runs = get_eval_run(generation_id=generation_id, session=session) or []
            
            # Build generation info (while still in session)
            created_time_iso = _to_utc_iso(generation.created_time) if generation.created_time else None
            name_display = generation.name or f"{generation.generation_type} Generation"
            method_display = generation.generation_method or "N/A"
            source_display = generation.source_id or "N/A"
            source_type_display = generation.source_type or "N/A"
            prompt_display = generation.prompt or "N/A"
            
            # Format meta as JSON (includes prompt if it's stored there)
            meta_json = json.dumps(generation.meta, indent=2) if generation.meta else "{}"
            meta_html = f'<pre style="margin: 0; white-space: pre-wrap; font-size: 12px; background: #f9f9f9; padding: 10px; border: 1px solid #ddd; border-radius: 4px; max-height: 300px; overflow-y: auto;">{html_escape(meta_json)}</pre>'
            
            # Format source_filters as JSON if available
            source_filters_json = json.dumps(generation.source_filters, indent=2) if generation.source_filters else None
            source_filters_html = f'<pre style="margin: 0; white-space: pre-wrap; font-size: 12px; background: #f9f9f9; padding: 10px; border: 1px solid #ddd; border-radius: 4px; max-height: 200px; overflow-y: auto;">{html_escape(source_filters_json)}</pre>' if source_filters_json else "N/A"
            
            generation_info = f"""
        <div class="card">
            <h2>Generation Details</h2>
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <tbody>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; width: 150px;">ID</td>
                        <td style="padding: 8px;"><code>{generation.id}</code></td>
                    </tr>
                    {f'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 8px; font-weight: 600;">Name</td><td style="padding: 8px;">{html_escape(name_display)}</td></tr>' if generation.name else ''}
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Type</td>
                        <td style="padding: 8px;">{html_escape(generation.generation_type)}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Method</td>
                        <td style="padding: 8px;">{html_escape(method_display)}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Source ID</td>
                        <td style="padding: 8px;">{html_escape(source_display)}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Source Type</td>
                        <td style="padding: 8px;">{html_escape(source_type_display)}</td>
                    </tr>
                    {f'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 8px; font-weight: 600;">Source Filters</td><td style="padding: 8px;">{source_filters_html}</td></tr>' if generation.source_filters else ''}
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; vertical-align: top;">Meta</td>
                        <td style="padding: 8px;">{meta_html}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; font-weight: 600;">Created</td>
                        <td style="padding: 8px;"><span class="utc-timestamp" data-iso="{created_time_iso or ''}">{created_time_iso or 'N/A'}</span></td>
                    </tr>
                </tbody>
            </table>
        </div>
        """

            # Build questions list (while still in session)
            questions_html = ""
            if questions:
                for idx, question in enumerate(questions):
                    question_preview = question.question[:150] + "..." if len(question.question) > 150 else question.question
                    created_time_q_iso = _to_utc_iso(question.created_time) if question.created_time else None
                    # Get persona from meta if available (for persona method)
                    persona_display = ""
                    if generation.generation_method == "persona" and question.meta:
                        persona = question.meta.get("persona")
                        if persona:
                            persona_display = f'<span style="background: #e3f2fd; padding: 3px 8px; border-radius: 3px; font-size: 12px; color: #1976d2;">{html_escape(persona)}</span>'
                    
                    questions_html += f"""
                <div class="card" style="margin-bottom: 15px; cursor: pointer; transition: background-color 0.2s;" 
                     onclick="window.location.href='/web/eval/question/{question.id}'"
                     onmouseover="this.style.backgroundColor='#f5f5f5'"
                     onmouseout="this.style.backgroundColor='white'">
                    <h3 style="margin-top: 0;">
                        <a href="/web/eval/question/{question.id}" style="text-decoration: none; color: inherit;">
                            Question #{idx + 1}
                        </a>
                    </h3>
                    <div style="margin-top: 10px;">
                        <div style="margin-bottom: 10px;">
                            <strong>Question:</strong> {html_escape(question_preview)}
                        </div>
                        <div style="display: flex; gap: 15px; align-items: center; color: #666; font-size: 14px; flex-wrap: wrap;">
                            <div><strong>Created:</strong> <span class="utc-timestamp" data-iso="{created_time_q_iso or ''}">{created_time_q_iso or 'N/A'}</span></div>
                            {f'<div>{persona_display}</div>' if persona_display else ''}
                        </div>
                    </div>
                </div>
                """
            else:
                questions_html = '<div class="info-box">No questions found for this generation.</div>'

            # Build runs list (while still in session)
            runs_html = ""
            if runs:
                runs_html = """
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <thead>
                    <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Name</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Embedding</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Max Results</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Created</th>
                    </tr>
                </thead>
                <tbody>
                """
                for run in runs:
                    created_time_iso = _to_utc_iso(run.created_time) if run.created_time else None
                    name_display = run.name or f"Run {run.id[:8]}"
                    embedding_display = run.embedding_name or "—"
                    max_results_display = run.max_results or "—"
                    
                    runs_html += f"""
                    <tr style="border-bottom: 1px solid #eee; cursor: pointer; transition: background-color 0.15s;" 
                        onclick="window.location.href='/web/eval/run/{run.id}'"
                        onmouseover="this.style.backgroundColor='#f8f9fa'"
                        onmouseout="this.style.backgroundColor='transparent'">
                        <td style="padding: 12px 10px;">
                            <a href="/web/eval/run/{run.id}" style="text-decoration: none; color: #2196F3; font-weight: 500;">
                                {html_escape(name_display)}
                            </a>
                        </td>
                        <td style="padding: 12px 10px; color: #666;">{html_escape(embedding_display)}</td>
                        <td style="padding: 12px 10px; color: #666;">{max_results_display}</td>
                        <td style="padding: 12px 10px; color: #666;">
                            <span class="utc-timestamp" data-iso="{created_time_iso or ''}">{created_time_iso or 'N/A'}</span>
                        </td>
                    </tr>
                    """
                runs_html += """
                </tbody>
            </table>
                """
            else:
                runs_html = '<div class="info-box">No evaluation runs found for this generation.</div>'

        content = f"""
        <h1>Eval Dataset</h1>
        <p><a href="/web/eval">← Back to Evaluation Overview</a></p>
        
        {generation_info}
        
        <div class="card" style="margin-top: 20px;">
            <h2>Eval Runs ({len(runs)})</h2>
            {runs_html}
        </div>
        
        <div class="card" style="margin-top: 20px;">
            <h2>Questions ({len(questions)})</h2>
            {questions_html}
        </div>
        <script>
        // Format UTC timestamps on page load
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.utc-timestamp').forEach(function(element) {{
                const isoString = element.getAttribute('data-iso');
                if (isoString) {{
                    element.textContent = formatLocalTime(isoString);
                }}
            }});
        }});
        
        // Toggle function for collapsible fields
        function toggleMetaField(fieldId) {{
            const collapsed = document.getElementById(fieldId + '-collapsed');
            const expanded = document.getElementById(fieldId + '-expanded');
            if (collapsed.style.display === 'none') {{
                collapsed.style.display = 'block';
                expanded.style.display = 'none';
            }} else {{
                collapsed.style.display = 'none';
                expanded.style.display = 'block';
            }}
        }}
        </script>
        """

        return HTMLResponse(html_templates.base_template(
            "Eval Dataset - MCP Server",
            content,
            None,
            username
        ))
    app.add_route("/web/eval/generation/{generation_id}", web_eval_generation)

    async def web_eval_question(request: Request):
        """Eval question detail page showing question and audits."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        
        # Get username from authenticated session
        username = session_data.get("username")

        question_id = request.path_params["question_id"]

        # Get question
        from ....kb.eval.db_models import get_eval_questions, EvalResult
        from ....kb.eval.audit import get_question_audits
        from ....kb.database import get_db_session_ephemeral
        
        with get_db_session_ephemeral() as session:
            question = get_eval_questions(question_id=question_id, session=session)
            
            if not question:
                return HTMLResponse(
                    html_templates.base_template(
                        "Question Not Found",
                        '<div class="error-box"><h2>Question Not Found</h2><p>The requested evaluation question could not be found.</p><p><a href="/web/eval">← Back to Evaluation Overview</a></p></div>',
                        None,
                        username
                    ),
                    status_code=404
                )

            # Get audits
            audits = get_question_audits(question_id=question.id, session=session)
            
            # Get all results for this question
            results = session.query(EvalResult).options(
                joinedload(EvalResult.run)
            ).filter(EvalResult.question_id == question.id).order_by(EvalResult.created_time.desc()).all()
            
            # Build question info (while still in session)
            created_time_iso = _to_utc_iso(question.created_time) if question.created_time else None
            generation_time_display = f"{question.generation_time_seconds:.2f}s" if question.generation_time_seconds else "—"
            hostname_display = question.hostname or "—"
            
            # Build metadata display (excluding index)
            meta_html = ""
            if question.meta:
                # Filter out index from display
                filtered_meta = {k: v for k, v in question.meta.items() if k != "index"}
                if filtered_meta:
                    meta_html = '<div class="card" style="margin-top: 15px;"><h2>Metadata</h2><div style="max-height: 400px; overflow-y: auto; border: 1px solid #ddd; border-radius: 4px;"><table style="width: 100%;"><tr><th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd; position: sticky; top: 0; background: white;">Key</th><th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd; position: sticky; top: 0; background: white;">Value</th></tr>'
                    field_index = 0
                    for key, value in filtered_meta.items():
                        field_id = f"meta-field-{field_index}"
                        # Format value based on type
                        if isinstance(value, list):
                            list_items = ''.join(f'<li>{html_escape(str(v))}</li>' for v in value)
                            formatted_value = f'<ul style="margin: 0; padding-left: 20px;">{list_items}</ul>'
                            full_value = formatted_value
                        elif isinstance(value, dict):
                            formatted_value = f'<pre style="margin: 0; white-space: pre-wrap;">{html_escape(json.dumps(value, indent=2))}</pre>'
                            full_value = formatted_value
                        elif isinstance(value, (str, int, float, bool)):
                            formatted_value = html_escape(str(value))
                            full_value = formatted_value
                        else:
                            formatted_value = html_escape(str(value))
                            full_value = f'<pre style="margin: 0;">{formatted_value}</pre>'
                        
                        # Check if content needs expansion
                        is_html = isinstance(value, (list, dict))
                        is_long_text = isinstance(value, str) and len(str(value)) > 200
                        needs_expansion = is_html or is_long_text
                        
                        if needs_expansion:
                            if is_html:
                                collapsed_style = "max-height: 1.5em; overflow: hidden;"
                            else:
                                collapsed_style = "white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;"
                            
                            meta_html += f'''
                            <tr>
                                <td style="padding: 8px; border-bottom: 1px solid #eee; vertical-align: top;"><strong>{html_escape(str(key))}</strong></td>
                                <td style="padding: 8px; border-bottom: 1px solid #eee; position: relative;">
                                    <div id="{field_id}-collapsed" style="display: block;">
                                        <div style="{collapsed_style}">{full_value}</div>
                                        <span onclick="toggleMetaField('{field_id}')" style="position: absolute; top: 8px; right: 8px; cursor: pointer; font-size: 16px; color: #666; user-select: none;">+</span>
                                    </div>
                                    <div id="{field_id}-expanded" style="display: none;">
                                        <div>{full_value}</div>
                                        <span onclick="toggleMetaField('{field_id}')" style="position: absolute; top: 8px; right: 8px; cursor: pointer; font-size: 16px; color: #666; user-select: none;">−</span>
                                    </div>
                                </td>
                            </tr>
                            '''
                        else:
                            meta_html += f'<tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>{html_escape(str(key))}</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{full_value}</td></tr>'
                        field_index += 1
                    meta_html += '</table></div></div>'
                    meta_html += '''
                    <script>
                    function toggleMetaField(fieldId) {
                        const collapsed = document.getElementById(fieldId + '-collapsed');
                        const expanded = document.getElementById(fieldId + '-expanded');
                        if (collapsed.style.display === 'none') {
                            collapsed.style.display = 'block';
                            expanded.style.display = 'none';
                        } else {
                            collapsed.style.display = 'none';
                            expanded.style.display = 'block';
                        }
                    }
                    </script>
                    '''
            
            question_info = f"""
        <div class="card">
            <h2>Question Details</h2>
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 15px;">
                <tbody>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; width: 150px;">ID</td>
                        <td style="padding: 8px;"><code style="font-size: 12px;">{question.id}</code></td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Created</td>
                        <td style="padding: 8px;"><span class="utc-timestamp" data-iso="{created_time_iso or ''}">{created_time_iso or 'N/A'}</span></td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Gen Time</td>
                        <td style="padding: 8px;">{generation_time_display}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Hostname</td>
                        <td style="padding: 8px;">{html_escape(hostname_display)}</td>
                    </tr>
                    {f'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 8px; font-weight: 600;">Generation</td><td style="padding: 8px;"><a href="/web/eval/generation/{question.generation_id}" style="color: #2196F3; text-decoration: underline;">View</a></td></tr>' if question.generation_id else ''}
                    {f'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 8px; font-weight: 600;">Source Doc</td><td style="padding: 8px;"><a href="/web/document/{question.source_document_id}" style="color: #2196F3; text-decoration: underline;">View</a></td></tr>' if question.source_document_id else ''}
                </tbody>
            </table>
            <div style="margin-bottom: 15px;">
                <strong>Question:</strong>
                <div style="margin-top: 8px; padding: 12px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; white-space: pre-wrap; font-size: 14px;">{html_escape(question.question)}</div>
            </div>
            {f'<div style="margin-top: 12px;"><strong>Answer:</strong><div style="margin-top: 8px; padding: 12px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; white-space: pre-wrap; font-size: 14px;">{html_escape(question.answer)}</div></div>' if question.answer else ''}
            {f'<div style="margin-top: 12px;"><strong>Keypoints:</strong><div style="margin-top: 8px; padding: 12px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; white-space: pre-wrap; font-size: 14px;">{html_escape(question.keypoints)}</div></div>' if question.keypoints else ''}
        </div>
        {meta_html}
        """

            # Build audits list (while still in session)
            audits_html = ""
            if audits:
                for audit in audits:
                    created_time_a_iso = _to_utc_iso(audit.created_time) if audit.created_time else None
                    is_valid_display = '<span style="color: green;">Valid</span>' if audit.is_valid else '<span style="color: red;">Invalid</span>'
                    score_display = f" ({audit.score:.2f})" if audit.score is not None else ""
                    auditor_display = audit.auditor_name or "N/A"
                    comments_display = audit.comments or "No comments"
                    
                    audits_html += f"""
                <div class="card" style="margin-bottom: 15px;">
                    <h3 style="margin-top: 0;">
                        {is_valid_display} - {html_escape(audit.audit_type)}{score_display}
                    </h3>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-top: 10px; color: #666; font-size: 14px;">
                        <div><strong>Auditor:</strong> {html_escape(auditor_display)}</div>
                        <div><strong>Created:</strong> <span class="utc-timestamp" data-iso="{created_time_a_iso or ''}">{created_time_a_iso or 'N/A'}</span></div>
                    </div>
                    <div style="margin-top: 10px;">
                        <strong>Comments:</strong>
                        <div style="margin-top: 5px; padding: 10px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; white-space: pre-wrap;">{html_escape(comments_display)}</div>
                    </div>
                </div>
                """
            else:
                audits_html = '<div class="info-box">No audits found for this question.</div>'
            
            # Build results list (while still in session)
            results_html = ""
            if results:
                results_html = """
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <thead>
                    <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Run</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Doc Hit</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Judge Hit</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Rank</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Similarity</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Retrieval Time</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Created</th>
                    </tr>
                </thead>
                <tbody>
                """
                for result in results:
                    # Run name/link
                    if result.run:
                        run_name = result.run.name or f"Run {result.run.id[:8]}"
                        run_link = f'<a href="/web/eval/run/{result.run_id}" style="text-decoration: none; color: #2196F3;">{html_escape(run_name)}</a>'
                    else:
                        run_link = f'<a href="/web/eval/run/{result.run_id}" style="text-decoration: none; color: #2196F3;">View Run</a>'
                    
                    # Document hit status
                    doc_hit_display = "✓" if result.is_hit else "✗"
                    doc_hit_color = "#4caf50" if result.is_hit else "#f44336"
                    
                    # Judge hit status
                    if result.is_judge_hit is not None:
                        judge_hit_display = "✓" if result.is_judge_hit else "✗"
                        judge_hit_color = "#4caf50" if result.is_judge_hit else "#f44336"
                    else:
                        judge_hit_display = "—"
                        judge_hit_color = "#999"
                    
                    # Rank
                    rank_display = str(result.hit_rank) if result.hit_rank else "—"
                    
                    # Similarity
                    similarity_display = f"{result.best_similarity:.3f}" if result.best_similarity is not None else "—"
                    
                    # Retrieval time
                    retrieval_time_display = f"{result.retrieval_time_seconds:.3f}s" if result.retrieval_time_seconds else "—"
                    
                    # Created time
                    created_time_r_iso = _to_utc_iso(result.created_time) if result.created_time else None
                    
                    results_html += f"""
                    <tr style="border-bottom: 1px solid #eee; cursor: pointer; transition: background-color 0.15s;" 
                        onclick="window.location.href='/web/eval/result/{result.id}'"
                        onmouseover="this.style.backgroundColor='#f8f9fa'"
                        onmouseout="this.style.backgroundColor='transparent'">
                        <td style="padding: 12px 10px;">{run_link}</td>
                        <td style="padding: 12px 10px; color: {doc_hit_color}; font-weight: 600;">{doc_hit_display}</td>
                        <td style="padding: 12px 10px; color: {judge_hit_color}; font-weight: 600;">{judge_hit_display}</td>
                        <td style="padding: 12px 10px; color: #666;">{rank_display}</td>
                        <td style="padding: 12px 10px; color: #666;">{similarity_display}</td>
                        <td style="padding: 12px 10px; color: #666;">{retrieval_time_display}</td>
                        <td style="padding: 12px 10px; color: #666;">
                            <span class="utc-timestamp" data-iso="{created_time_r_iso or ''}">{created_time_r_iso or 'N/A'}</span>
                        </td>
                    </tr>
                    """
                results_html += """
                </tbody>
            </table>
                """
            else:
                results_html = '<div class="info-box">No evaluation results found for this question.</div>'
            
            # Build navigation (while still in session)
            nav_links = '<p>'
            if question.generation_id:
                nav_links += f'<a href="/web/eval/generation/{question.generation_id}">← Back to Generation</a> | '
            nav_links += '<a href="/web/eval">← Back to Evaluation Overview</a>'
            nav_links += '</p>'

        content = f"""
        <h1>Evaluation Question</h1>
        {nav_links}
        
        {question_info}
        
        <div class="card" style="margin-top: 20px;">
            <h2>Evaluation Results ({len(results)})</h2>
            {results_html}
        </div>
        
        <div class="card" style="margin-top: 20px;">
            <h2>Audits ({len(audits)})</h2>
            {audits_html}
        </div>
        <script>
        // Format UTC timestamps on page load
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.utc-timestamp').forEach(function(element) {{
                const isoString = element.getAttribute('data-iso');
                if (isoString) {{
                    element.textContent = formatLocalTime(isoString);
                }}
            }});
        }});
        </script>
        """

        return HTMLResponse(html_templates.base_template(
            "Evaluation Question - MCP Server",
            content,
            None,
            username
        ))
    app.add_route("/web/eval/question/{question_id}", web_eval_question)

    async def web_eval_run(request: Request):
        """Eval run detail page showing run details, summary stats, and results."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        
        # Get username from authenticated session
        username = session_data.get("username")

        run_id = request.path_params["run_id"]

        # Get run, results, and summary stats
        from ....kb.eval.db_models import get_eval_run
        from ....kb.eval.runner import get_run_results
        from ....kb.eval.metrics import get_summary_stats
        from ....kb.database import get_db_session_ephemeral
        
        with get_db_session_ephemeral() as session:
            run = get_eval_run(run_id=run_id, session=session)
            
            if not run:
                return HTMLResponse(
                    html_templates.base_template(
                        "Run Not Found",
                        '<div class="error-box"><h2>Run Not Found</h2><p>The requested evaluation run could not be found.</p><p><a href="/web/eval">← Back to Evaluation Overview</a></p></div>',
                        None,
                        username
                    ),
                    status_code=404
                )

            # Get results for this run
            results = get_run_results(run_id=run_id, session=session) or []
            
            # Get summary stats for all questions (includes both doc match and judge stats)
            summary_stats_all = get_summary_stats(run_id=run_id, use_judge=False, session=session)
            
            # Get summary stats filtered by audit type (includes both doc match and judge stats)
            summary_stats_human = get_summary_stats(run_id=run_id, use_judge=False, audit_type="human_review", session=session)
            summary_stats_llm = get_summary_stats(run_id=run_id, use_judge=False, audit_type="llm_judge", session=session)
            
            # Build run info (while still in session)
            created_time_iso = _to_utc_iso(run.created_time) if run.created_time else None
            name_display = run.name or f"Run {run.id[:8]}"
            embedding_display = run.embedding_name or "N/A"
            chunking_display = run.chunking_strategy or "N/A"
            max_results_display = run.max_results or "N/A"
            generation_link = ""
            if run.generation_id:
                if run.generation:
                    gen_name = run.generation.name or f"{run.generation.generation_type} Generation"
                    generation_link = f'<a href="/web/eval/generation/{run.generation_id}" style="text-decoration: none; color: #2196F3;">{html_escape(gen_name)}</a>'
                else:
                    generation_link = f'<a href="/web/eval/generation/{run.generation_id}" style="text-decoration: none; color: #2196F3;">View</a>'
            else:
                generation_link = "N/A"
            
            run_info = f"""
        <div class="card">
            <h2>Run Details</h2>
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <tbody>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; width: 150px;">ID</td>
                        <td style="padding: 8px;"><code>{run.id}</code></td>
                    </tr>
                    {f'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 8px; font-weight: 600;">Name</td><td style="padding: 8px;">{html_escape(name_display)}</td></tr>' if run.name else ''}
                    {f'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 8px; font-weight: 600;">Description</td><td style="padding: 8px;">{html_escape(run.description)}</td></tr>' if run.description else ''}
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Generation</td>
                        <td style="padding: 8px;">{generation_link}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Embedding</td>
                        <td style="padding: 8px;">{html_escape(embedding_display)}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Chunking Strategy</td>
                        <td style="padding: 8px;">{html_escape(chunking_display)}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Max Results</td>
                        <td style="padding: 8px;">{max_results_display}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; font-weight: 600;">Created</td>
                        <td style="padding: 8px;"><span class="utc-timestamp" data-iso="{created_time_iso or ''}">{created_time_iso or 'N/A'}</span></td>
                    </tr>
                </tbody>
            </table>
        </div>
        """
            
            # Extract stats for all questions
            all_doc_total = summary_stats_all.get("total_questions", 0)
            all_doc_hits = summary_stats_all.get("hits", 0)
            all_doc_misses = summary_stats_all.get("misses", 0)
            all_doc_hit_rate = summary_stats_all.get("hit_rate", 0.0) * 100
            all_judge_total = summary_stats_all.get("judge_total_questions", 0)
            all_judge_hits = summary_stats_all.get("judge_hits", 0)
            all_judge_misses = summary_stats_all.get("judge_misses", 0)
            all_judge_hit_rate = summary_stats_all.get("judge_hit_rate", 0.0) * 100 if "judge_hit_rate" in summary_stats_all else 0.0
            
            # Extract stats for human review filtered
            human_doc_total = summary_stats_human.get("total_questions", 0)
            human_doc_hits = summary_stats_human.get("hits", 0)
            human_doc_misses = summary_stats_human.get("misses", 0)
            human_doc_hit_rate = summary_stats_human.get("hit_rate", 0.0) * 100
            human_judge_total = summary_stats_human.get("judge_total_questions", 0)
            human_judge_hits = summary_stats_human.get("judge_hits", 0)
            human_judge_misses = summary_stats_human.get("judge_misses", 0)
            human_judge_hit_rate = summary_stats_human.get("judge_hit_rate", 0.0) * 100 if "judge_hit_rate" in summary_stats_human else 0.0
            
            # Extract stats for LLM judge audit filtered
            llm_doc_total = summary_stats_llm.get("total_questions", 0)
            llm_doc_hits = summary_stats_llm.get("hits", 0)
            llm_doc_misses = summary_stats_llm.get("misses", 0)
            llm_doc_hit_rate = summary_stats_llm.get("hit_rate", 0.0) * 100
            llm_judge_total = summary_stats_llm.get("judge_total_questions", 0)
            llm_judge_hits = summary_stats_llm.get("judge_hits", 0)
            llm_judge_misses = summary_stats_llm.get("judge_misses", 0)
            llm_judge_hit_rate = summary_stats_llm.get("judge_hit_rate", 0.0) * 100 if "judge_hit_rate" in summary_stats_llm else 0.0

            # Compute "with answer" stats inline from loaded results
            answered_results = [r for r in results if r.meta and r.meta.get("llm_answer")]
            ans_doc_total = len(answered_results)
            ans_doc_hits = sum(1 for r in answered_results if r.is_hit)
            ans_doc_misses = ans_doc_total - ans_doc_hits
            ans_doc_hit_rate = (ans_doc_hits / ans_doc_total * 100) if ans_doc_total > 0 else 0.0
            ans_judge_results = [r for r in answered_results if r.is_judge_hit is not None]
            ans_judge_total = len(ans_judge_results)
            ans_judge_hits = sum(1 for r in ans_judge_results if r.is_judge_hit)
            ans_judge_misses = ans_judge_total - ans_judge_hits
            ans_judge_hit_rate = (ans_judge_hits / ans_judge_total * 100) if ans_judge_total > 0 else 0.0
            
            summary_html = f"""
        <div class="card" style="margin-top: 20px;">
            <h2>Summary Statistics</h2>
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <thead>
                    <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                        <th style="text-align: left; padding: 10px; font-weight: 600; width: 200px;">Metric</th>
                        <th style="text-align: center; padding: 10px; font-weight: 600; background-color: #e8f4f8;">All Questions</th>
                        <th style="text-align: center; padding: 10px; font-weight: 600; background-color: #fff3cd;">Human Review<br/>(Valid Only)</th>
                        <th style="text-align: center; padding: 10px; font-weight: 600; background-color: #d1ecf1;">LLM Review<br/>(Valid Only)</th>
                        <th style="text-align: center; padding: 10px; font-weight: 600; background-color: #e8f5e9;">With Answer</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom: 2px solid #ddd; background-color: #f9f9f9;">
                        <td style="padding: 8px; font-weight: 700;">Total Questions</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;">{all_doc_total}</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;">{human_doc_total}</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;">{llm_doc_total}</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;">{ans_doc_total}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; padding-left: 20px;">Document Match Hits</td>
                        <td style="padding: 8px; text-align: center;">{all_doc_hits}</td>
                        <td style="padding: 8px; text-align: center;">{human_doc_hits}</td>
                        <td style="padding: 8px; text-align: center;">{llm_doc_hits}</td>
                        <td style="padding: 8px; text-align: center;">{ans_doc_hits}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; padding-left: 20px;">Document Match Misses</td>
                        <td style="padding: 8px; text-align: center;">{all_doc_misses}</td>
                        <td style="padding: 8px; text-align: center;">{human_doc_misses}</td>
                        <td style="padding: 8px; text-align: center;">{llm_doc_misses}</td>
                        <td style="padding: 8px; text-align: center;">{ans_doc_misses}</td>
                    </tr>
                    <tr style="border-bottom: 2px solid #ddd; background-color: #f9f9f9;">
                        <td style="padding: 8px; font-weight: 700;">Document Match Hit Rate</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;"><strong>{all_doc_hit_rate:.1f}%</strong></td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;"><strong>{human_doc_hit_rate:.1f}%</strong></td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;"><strong>{llm_doc_hit_rate:.1f}%</strong></td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;"><strong>{ans_doc_hit_rate:.1f}%</strong></td>
                    </tr>
                    <tr style="border-bottom: 2px solid #ddd; background-color: #f0f7ff;">
                        <td style="padding: 8px; font-weight: 700; padding-top: 15px;">LLM Judge Evaluated</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600; padding-top: 15px;">{all_judge_total if all_judge_total > 0 else "—"}</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600; padding-top: 15px;">{human_judge_total if human_judge_total > 0 else "—"}</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600; padding-top: 15px;">{llm_judge_total if llm_judge_total > 0 else "—"}</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600; padding-top: 15px;">{ans_judge_total if ans_judge_total > 0 else "—"}</td>
                    </tr>
                    {f'''<tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; padding-left: 20px;">LLM Judge Hits</td>
                        <td style="padding: 8px; text-align: center;">{all_judge_hits}</td>
                        <td style="padding: 8px; text-align: center;">{human_judge_hits}</td>
                        <td style="padding: 8px; text-align: center;">{llm_judge_hits}</td>
                        <td style="padding: 8px; text-align: center;">{ans_judge_hits}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; padding-left: 20px;">LLM Judge Misses</td>
                        <td style="padding: 8px; text-align: center;">{all_judge_misses}</td>
                        <td style="padding: 8px; text-align: center;">{human_judge_misses}</td>
                        <td style="padding: 8px; text-align: center;">{llm_judge_misses}</td>
                        <td style="padding: 8px; text-align: center;">{ans_judge_misses}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee; background-color: #f0f7ff;">
                        <td style="padding: 8px; font-weight: 700;">LLM Judge Hit Rate</td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;"><strong>{all_judge_hit_rate:.1f}%</strong></td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;"><strong>{human_judge_hit_rate:.1f}%</strong></td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;"><strong>{llm_judge_hit_rate:.1f}%</strong></td>
                        <td style="padding: 8px; text-align: center; font-weight: 600;"><strong>{ans_judge_hit_rate:.1f}%</strong></td>
                    </tr>''' if all_judge_total > 0 or human_judge_total > 0 or llm_judge_total > 0 or ans_judge_total > 0 else ''}
                </tbody>
            </table>
        </div>
        """
            
            # Build results list (while still in session)
            results_html = ""
            if results:
                results_html = """
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <thead>
                    <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Question</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Doc Hit</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Judge Hit</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Answer</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Rank</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Similarity</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Retrieval Time</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">View</th>
                    </tr>
                </thead>
                <tbody>
                """
                for result in results:
                    # Get question text
                    question_text = ""
                    if result.question:
                        question_text = result.question.question[:100] + "..." if len(result.question.question) > 100 else result.question.question
                        question_link = f'<a href="/web/eval/question/{result.question_id}" style="text-decoration: none; color: #2196F3;">{html_escape(question_text)}</a>'
                    else:
                        question_link = f'<a href="/web/eval/question/{result.question_id}" style="text-decoration: none; color: #2196F3;">Question {result.question_id[:8]}</a>'
                    
                    # Document hit status
                    doc_hit_display = "✓" if result.is_hit else "✗"
                    doc_hit_color = "#4caf50" if result.is_hit else "#f44336"
                    
                    # Judge hit status
                    if result.is_judge_hit is not None:
                        judge_hit_display = "✓" if result.is_judge_hit else "✗"
                        judge_hit_color = "#4caf50" if result.is_judge_hit else "#f44336"
                    else:
                        judge_hit_display = "—"
                        judge_hit_color = "#999"
                    
                    # Rank
                    rank_display = str(result.hit_rank) if result.hit_rank else "—"
                    
                    # Similarity
                    similarity_display = f"{result.best_similarity:.3f}" if result.best_similarity is not None else "—"
                    
                    # Retrieval time
                    retrieval_time_display = f"{result.retrieval_time_seconds:.3f}s" if result.retrieval_time_seconds else "—"

                    # Answer present
                    has_answer = bool(result.meta and result.meta.get("llm_answer"))
                    answer_display = "✓" if has_answer else "—"
                    answer_color = "#4caf50" if has_answer else "#bbb"

                    results_html += f"""
                    <tr class="result-row" data-has-answer="{'true' if has_answer else 'false'}" style="border-bottom: 1px solid #eee;">
                        <td style="padding: 12px 10px; cursor: pointer;" onclick="window.location.href='/web/eval/question/{result.question_id}'" onmouseover="this.style.backgroundColor='#f8f9fa'" onmouseout="this.style.backgroundColor='transparent'">{question_link}</td>
                        <td style="padding: 12px 10px; color: {doc_hit_color}; font-weight: 600;">{doc_hit_display}</td>
                        <td style="padding: 12px 10px; color: {judge_hit_color}; font-weight: 600;">{judge_hit_display}</td>
                        <td style="padding: 12px 10px; color: {answer_color}; font-weight: 600;">{answer_display}</td>
                        <td style="padding: 12px 10px; color: #666;">{rank_display}</td>
                        <td style="padding: 12px 10px; color: #666;">{similarity_display}</td>
                        <td style="padding: 12px 10px; color: #666;">{retrieval_time_display}</td>
                        <td style="padding: 12px 10px;">
                            <a href="/web/eval/result/{result.id}" style="text-decoration: none; color: #2196F3; font-weight: 500;">View</a>
                        </td>
                    </tr>
                    """
                results_html += """
                </tbody>
            </table>
                """
            else:
                results_html = '<div class="info-box">No results found for this run.</div>'

        content = f"""
        <h1>Evaluation Run</h1>
        <p><a href="/web/eval">← Back to Evaluation Overview</a></p>

        {run_info}
        {summary_html}

        <div class="card" style="margin-top: 20px;">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;">
                <h2 style="margin: 0;">Results ({len(results)})</h2>
                <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; color: #555;">
                    <input type="checkbox" id="filterAnswered" onchange="filterResults()" style="width: 16px; height: 16px; cursor: pointer;">
                    Show only results with an answer
                </label>
            </div>
            {results_html}
        </div>
        <script>
        function filterResults() {{
            const onlyAnswered = document.getElementById('filterAnswered').checked;
            document.querySelectorAll('tr.result-row').forEach(function(row) {{
                if (onlyAnswered && row.getAttribute('data-has-answer') !== 'true') {{
                    row.style.display = 'none';
                }} else {{
                    row.style.display = '';
                }}
            }});
        }}
        // Format UTC timestamps on page load
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.utc-timestamp').forEach(function(element) {{
                const isoString = element.getAttribute('data-iso');
                if (isoString) {{
                    element.textContent = formatLocalTime(isoString);
                }}
            }});
        }});
        </script>
        """

        return HTMLResponse(html_templates.base_template(
            "Evaluation Run - MCP Server",
            content,
            None,
            username
        ))
    app.add_route("/web/eval/run/{run_id}", web_eval_run)

    async def web_eval_result(request: Request):
        """Eval result detail page showing result details, question link, and retrieved documents."""
        # Check authentication first
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        
        # Get username from authenticated session
        username = session_data.get("username")

        result_id = request.path_params["result_id"]

        # Get result with relationships
        from ....kb.eval.db_models import EvalResult, EvalRetrievedDocument
        from ....kb.database import get_db_session_ephemeral
        
        with get_db_session_ephemeral() as session:
            result = session.query(EvalResult).options(
                joinedload(EvalResult.run),
                joinedload(EvalResult.question),
                joinedload(EvalResult.retrieved_docs).joinedload(EvalRetrievedDocument.document)
            ).filter(EvalResult.id == result_id).first()
            
            if not result:
                return HTMLResponse(
                    html_templates.base_template(
                        "Result Not Found",
                        '<div class="error-box"><h2>Result Not Found</h2><p>The requested evaluation result could not be found.</p><p><a href="/web/eval">← Back to Evaluation Overview</a></p></div>',
                        None,
                        username
                    ),
                    status_code=404
                )

            # Build result info (while still in session)
            created_time_iso = _to_utc_iso(result.created_time) if result.created_time else None
            
            # Run link
            run_link = ""
            if result.run:
                run_name = result.run.name or f"Run {result.run.id[:8]}"
                run_link = f'<a href="/web/eval/run/{result.run_id}" style="text-decoration: none; color: #2196F3;">{html_escape(run_name)}</a>'
            else:
                run_link = f'<a href="/web/eval/run/{result.run_id}" style="text-decoration: none; color: #2196F3;">View Run</a>'
            
            # Question link
            question_link = ""
            if result.question:
                question_preview = result.question.question[:80] + "..." if len(result.question.question) > 80 else result.question.question
                question_link = f'<a href="/web/eval/question/{result.question_id}" style="text-decoration: none; color: #2196F3;">{html_escape(question_preview)}</a>'
            else:
                question_link = f'<a href="/web/eval/question/{result.question_id}" style="text-decoration: none; color: #2196F3;">View Question</a>'
            
            # Hit statuses
            doc_hit_display = "✓" if result.is_hit else "✗"
            doc_hit_color = "#4caf50" if result.is_hit else "#f44336"
            
            if result.is_judge_hit is not None:
                judge_hit_display = "✓" if result.is_judge_hit else "✗"
                judge_hit_color = "#4caf50" if result.is_judge_hit else "#f44336"
            else:
                judge_hit_display = "—"
                judge_hit_color = "#999"
            
            result_info = f"""
        <div class="card">
            <h2>Result Details</h2>
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <tbody>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600; width: 150px;">ID</td>
                        <td style="padding: 8px;"><code>{result.id}</code></td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Run</td>
                        <td style="padding: 8px;">{run_link}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Question</td>
                        <td style="padding: 8px;">{question_link}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Document Hit</td>
                        <td style="padding: 8px; color: {doc_hit_color}; font-weight: 600;">{doc_hit_display}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Judge Hit</td>
                        <td style="padding: 8px; color: {judge_hit_color}; font-weight: 600;">{judge_hit_display}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Hit Rank</td>
                        <td style="padding: 8px;">{result.hit_rank if result.hit_rank else "—"}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Best Similarity</td>
                        <td style="padding: 8px;">{f"{result.best_similarity:.3f}" if result.best_similarity is not None else "—"}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px; font-weight: 600;">Retrieval Time</td>
                        <td style="padding: 8px;">{f"{result.retrieval_time_seconds:.3f}s" if result.retrieval_time_seconds else "—"}</td>
                    </tr>
                    {f'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 8px; font-weight: 600;">Judge Time</td><td style="padding: 8px;">{result.judge_time_seconds:.3f}s</td></tr>' if result.judge_time_seconds else ''}
                    {f'<tr style="border-bottom: 1px solid #eee;"><td style="padding: 8px; font-weight: 600;">Hostname</td><td style="padding: 8px;">{html_escape(result.hostname)}</td></tr>' if result.hostname else ''}
                    <tr>
                        <td style="padding: 8px; font-weight: 600;">Created</td>
                        <td style="padding: 8px;"><span class="utc-timestamp" data-iso="{created_time_iso or ''}">{created_time_iso or 'N/A'}</span></td>
                    </tr>
                </tbody>
            </table>
            {f'<div style="margin-top: 15px;"><strong>Judge Justification:</strong><div style="margin-top: 8px; padding: 12px; background: #f9f9f9; border: 1px solid #ddd; border-radius: 4px; white-space: pre-wrap; font-size: 14px;">{html_escape(result.justification)}</div></div>' if result.justification else ''}
            {f'<div style="margin-top: 15px;"><strong>LLM Answer</strong> <span style="color: #999; font-size: 12px;">({result.meta.get("llm_answer_time", 0):.2f}s)</span>:<div style="margin-top: 8px; padding: 12px; background: #f0f7ff; border: 1px solid #b3d7ff; border-radius: 4px; white-space: pre-wrap; font-size: 14px;">{html_escape(result.meta.get("llm_answer", ""))}</div></div>' if result.meta and result.meta.get("llm_answer") else ''}
            {_render_agentic_conversation(result.meta.get("agentic_conversation")) if result.meta and result.meta.get("agentic_conversation") else ''}
        </div>
        """
            
            # Get retrieved documents count and list (while still in session)
            retrieved_docs_list = list(result.retrieved_docs) if result.retrieved_docs else []
            retrieved_docs_count = len(retrieved_docs_list)
            
            # Build retrieved documents list (while still in session)
            retrieved_docs_html = ""
            if retrieved_docs_list:
                retrieved_docs_html = """
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <thead>
                    <tr style="background-color: #f5f5f5; border-bottom: 2px solid #ddd;">
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Rank</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Document</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Similarity</th>
                        <th style="text-align: left; padding: 10px; font-weight: 600;">Chunk IDs</th>
                    </tr>
                </thead>
                <tbody>
                """
                for doc in retrieved_docs_list:
                    # Document link
                    if doc.document:
                        doc_title = doc.document.title or doc.document.id[:8]
                        doc_link = f'<a href="/web/document/{doc.document_id}" style="text-decoration: none; color: #2196F3;">{html_escape(doc_title)}</a>'
                    else:
                        doc_link = f'<a href="/web/document/{doc.document_id}" style="text-decoration: none; color: #2196F3;">View Document</a>' if doc.document_id else "—"
                    
                    # Similarity
                    similarity_display = f"{doc.similarity:.3f}" if doc.similarity is not None else "—"
                    
                    # Chunk IDs
                    chunk_ids_display = "—"
                    if doc.chunk_ids:
                        if isinstance(doc.chunk_ids, list):
                            chunk_ids_display = ", ".join([str(cid) for cid in doc.chunk_ids[:5]])
                            if len(doc.chunk_ids) > 5:
                                chunk_ids_display += f" (+{len(doc.chunk_ids) - 5} more)"
                        else:
                            chunk_ids_display = str(doc.chunk_ids)
                    
                    retrieved_docs_html += f"""
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 12px 10px; color: #666;">{doc.rank}</td>
                        <td style="padding: 12px 10px;">{doc_link}</td>
                        <td style="padding: 12px 10px; color: #666;">{similarity_display}</td>
                        <td style="padding: 12px 10px; color: #666; font-size: 12px;">{chunk_ids_display}</td>
                    </tr>
                    """
                retrieved_docs_html += """
                </tbody>
            </table>
                """
            else:
                retrieved_docs_html = '<div class="info-box">No retrieved documents found for this result.</div>'

        content = f"""
        <h1>Evaluation Result</h1>
        <p><a href="/web/eval">← Back to Evaluation Overview</a></p>
        
        {result_info}
        
        <div class="card" style="margin-top: 20px;">
            <h2>Retrieved Documents ({retrieved_docs_count})</h2>
            {retrieved_docs_html}
        </div>
        <script>
        // Format UTC timestamps on page load
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.utc-timestamp').forEach(function(element) {{
                const isoString = element.getAttribute('data-iso');
                if (isoString) {{
                    element.textContent = formatLocalTime(isoString);
                }}
            }});
        }});
        </script>
        """

        return HTMLResponse(html_templates.base_template(
            "Evaluation Result - MCP Server",
            content,
            None,
            username
        ))
    app.add_route("/web/eval/result/{result_id}", web_eval_result)
