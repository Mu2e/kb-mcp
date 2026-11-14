"""MCP usage page: how much the MCP server is used, as counts only.

Public (no admin login), like the document list: it shows calls per day and
tool and distinct sessions per day from logs_mcp_calls, never a session
identifier, query, user or error detail -- get_mcp_usage_daily() returns
nothing else.
"""

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .. import html_templates
from ..auth import WebSessionManager

logger = logging.getLogger(__name__)

RANGES = (7, 30, 90)
DEFAULT_RANGE = 30


def parse_days(value) -> int:
    """The requested range in days: one of RANGES, else the default."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RANGE
    return days if days in RANGES else DEFAULT_RANGE


# Tool names as the page labels them; anything else is shown by its name.
TOOL_LABELS = {
    "kb_search": "Searches (kb_search)",
    "kb_get": "Document fetches (kb_get)",
    "kb_get_image": "Image fetches (kb_get_image)",
}


def usage_page_html() -> str:
    """The page body; it draws everything from /api/usage in the browser."""
    buttons = "".join(
        f'<button type="button" class="btn usage-range" data-days="{d}">Last {d} days</button>'
        for d in RANGES
    )
    labels = ", ".join(f'"{k}": "{v}"' for k, v in TOOL_LABELS.items())
    return f"""
    <h1>MCP Usage</h1>
    <p style="color: #555; max-width: 60em;">
      How often the knowledge base's MCP server is used: tool calls per day
      and the number of distinct client sessions per day. Counts only &mdash;
      no users, queries or session identifiers are recorded or shown.
      Days are in US Central time.
    </p>
    <div style="display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0;">{buttons}</div>

    <div id="usage-cards" style="display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px;"></div>

    <div class="card">
      <h2 style="margin-top: 0;">Calls and sessions per day</h2>
      <div id="usage-legend" style="display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; margin-bottom: 8px;"></div>
      <div id="usage-chart" style="width: 100%; overflow-x: auto;"><div class="info-box">Loading&hellip;</div></div>
    </div>

    <div class="card">
      <h2 style="margin-top: 0;">By tool</h2>
      <div id="usage-tools"></div>
    </div>

    <style>
      .usage-range.active {{ background: #1565C0; }}
      .usage-card {{ flex: 1; min-width: 160px; background: #f7f9fc; border: 1px solid #e1e7ef; border-radius: 6px; padding: 12px 16px; }}
      .usage-card .v {{ font-size: 26px; font-weight: bold; color: #222; }}
      .usage-card .k {{ font-size: 13px; color: #555; }}
      #usage-tools table {{ border-collapse: collapse; width: 100%; max-width: 46em; }}
      #usage-tools th, #usage-tools td {{ padding: 6px 10px; border-bottom: 1px solid #e5e5e5; text-align: right; }}
      #usage-tools th:first-child, #usage-tools td:first-child {{ text-align: left; }}
    </style>

    <script>
    (function () {{
      const LABELS = {{{labels}}};
      const COLORS = ["#2196F3", "#4CAF50", "#FF9800", "#9C27B0", "#795548", "#607D8B"];
      const SESSION_COLOR = "#222";
      const label = t => LABELS[t] || t;
      const esc = s => String(s).replace(/[&<>"']/g, c => ({{"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}}[c]));
      const fmt = n => (n === null || n === undefined) ? "&ndash;" : Number(n).toLocaleString();

      function niceMax(v) {{
        if (v <= 5) return 5;
        const p = Math.pow(10, Math.floor(Math.log10(v)));
        for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
        return 10 * p;
      }}

      function cards(d) {{
        const by = d.totals.by_tool;
        const items = [
          [fmt(d.totals.calls), "tool calls"],
          [fmt(d.totals.sessions), "distinct sessions"],
          [fmt(by.kb_search || 0), "searches"],
          [fmt((by.kb_get || 0) + (by.kb_get_image || 0)), "document and image fetches"],
        ];
        document.getElementById("usage-cards").innerHTML = items.map(([v, k]) =>
          `<div class="usage-card"><div class="v">${{v}}</div><div class="k">${{k}} in the last ${{d.days}} days</div></div>`).join("");
      }}

      function chart(d) {{
        const tools = d.tools, series = d.series;
        const color = t => COLORS[tools.indexOf(t) % COLORS.length];
        document.getElementById("usage-legend").innerHTML =
          tools.map(t => `<span><span style="display:inline-block;width:12px;height:12px;background:${{color(t)}};margin-right:5px;vertical-align:-1px;"></span>${{esc(label(t))}}</span>`).join("") +
          `<span><span style="display:inline-block;width:16px;border-top:2px solid ${{SESSION_COLOR}};margin-right:5px;vertical-align:3px;"></span>Sessions (right axis)</span>`;
        if (!d.totals.calls) {{
          document.getElementById("usage-chart").innerHTML = '<div class="info-box">No MCP calls in this period.</div>';
          return;
        }}
        const W = Math.max(640, series.length * 14), H = 280, L = 48, R = 44, T = 10, B = 38;
        const iw = W - L - R, ih = H - T - B, bw = iw / series.length;
        const yMax = niceMax(Math.max(...series.map(s => s.total)));
        const sMax = niceMax(Math.max(1, ...series.map(s => s.sessions)));
        const y = v => T + ih - v / yMax * ih, ys = v => T + ih - v / sMax * ih;
        let svg = `<svg viewBox="0 0 ${{W}} ${{H}}" width="100%" style="min-width:${{W}}px;max-width:100%;font-family:sans-serif;font-size:11px;" role="img" aria-label="MCP calls and sessions per day">`;
        for (let i = 0; i <= 4; i++) {{
          const v = yMax * i / 4, yy = y(v);
          svg += `<line x1="${{L}}" x2="${{W - R}}" y1="${{yy}}" y2="${{yy}}" stroke="#e5e5e5"/>`;
          svg += `<text x="${{L - 6}}" y="${{yy + 4}}" text-anchor="end" fill="#666">${{Math.round(v)}}</text>`;
          svg += `<text x="${{W - R + 6}}" y="${{ys(sMax * i / 4) + 4}}" fill="#666">${{Math.round(sMax * i / 4)}}</text>`;
        }}
        const every = Math.ceil(series.length / 12);
        series.forEach((s, i) => {{
          let base = 0;
          const x = L + i * bw + bw * 0.12, w = bw * 0.76;
          for (const t of tools) {{
            const n = s.calls[t] || 0;
            if (!n) continue;
            svg += `<rect x="${{x}}" y="${{y(base + n)}}" width="${{w}}" height="${{y(base) - y(base + n)}}" fill="${{color(t)}}"><title>${{s.date}}: ${{n}} ${{esc(label(t))}}</title></rect>`;
            base += n;
          }}
          svg += `<rect x="${{L + i * bw}}" y="${{T}}" width="${{bw}}" height="${{ih}}" fill="transparent"><title>${{s.date}}: ${{s.total}} calls, ${{s.sessions}} sessions</title></rect>`;
          if (i % every === 0) svg += `<text x="${{L + i * bw + bw / 2}}" y="${{H - B + 16}}" text-anchor="middle" fill="#666">${{s.date.slice(5)}}</text>`;
        }});
        const pts = series.map((s, i) => `${{L + i * bw + bw / 2}},${{ys(s.sessions)}}`).join(" ");
        svg += `<polyline points="${{pts}}" fill="none" stroke="${{SESSION_COLOR}}" stroke-width="1.8"/>`;
        series.forEach((s, i) => {{ svg += `<circle cx="${{L + i * bw + bw / 2}}" cy="${{ys(s.sessions)}}" r="2.2" fill="${{SESSION_COLOR}}"/>`; }});
        svg += `<text x="${{L}}" y="${{H - 4}}" fill="#666">calls</text><text x="${{W - R}}" y="${{H - 4}}" fill="#666" text-anchor="end">sessions</text></svg>`;
        document.getElementById("usage-chart").innerHTML = svg;
      }}

      function tools(d) {{
        if (!d.tools.length) {{ document.getElementById("usage-tools").innerHTML = ""; return; }}
        let h = "<table><thead><tr><th>Tool</th><th>Calls</th><th>Share</th><th>Median time</th><th>95th percentile</th></tr></thead><tbody>";
        for (const t of d.tools) {{
          const n = d.totals.by_tool[t], lat = d.latency_ms[t] || {{}};
          const ms = v => v === null || v === undefined ? "&ndash;" : (v >= 1000 ? (v / 1000).toFixed(1) + " s" : v + " ms");
          h += `<tr><td>${{esc(label(t))}}</td><td>${{fmt(n)}}</td><td>${{Math.round(100 * n / d.totals.calls)}}%</td><td>${{ms(lat.median)}}</td><td>${{ms(lat.p95)}}</td></tr>`;
        }}
        h += "</tbody></table>";
        if (d.totals.errors) h += `<p style="font-size:13px;color:#666;">${{fmt(d.totals.errors)}} of these calls ended in an error.</p>`;
        document.getElementById("usage-tools").innerHTML = h;
      }}

      async function load(days) {{
        document.querySelectorAll(".usage-range").forEach(b => b.classList.toggle("active", Number(b.dataset.days) === days));
        try {{
          const r = await fetch("/api/usage?days=" + days);
          if (!r.ok) throw new Error("HTTP " + r.status);
          const d = await r.json();
          cards(d); chart(d); tools(d);
        }} catch (e) {{
          document.getElementById("usage-chart").innerHTML = `<div class="error-box">Could not load usage: ${{esc(e.message)}}</div>`;
        }}
      }}

      document.querySelectorAll(".usage-range").forEach(b => b.addEventListener("click", () => {{
        const days = Number(b.dataset.days);
        history.replaceState(null, "", "?days=" + days);
        load(days);
      }}));
      const q = Number(new URLSearchParams(location.search).get("days"));
      load([{", ".join(str(d) for d in RANGES)}].includes(q) ? q : {DEFAULT_RANGE});
    }})();
    </script>
    """


def setup_usage_routes(app, session_manager: WebSessionManager, require_auth_html, require_auth_api):
    """Register /web/usage (the page) and /api/usage (its data). Both public."""

    async def web_usage(request: Request):
        session_data, redirect = await require_auth_html(request, session_manager)
        if redirect:
            return redirect
        return HTMLResponse(html_templates.base_template(
            title="MCP Usage", content=usage_page_html(), username=(session_data or {}).get("username"),
        ))

    async def api_usage(request: Request):
        _session, error = await require_auth_api(request, session_manager, json_response=True)
        if error:
            return error
        from ....kb.logs import get_mcp_usage_daily

        days = parse_days(request.query_params.get("days"))
        try:
            data = await run_in_threadpool(get_mcp_usage_daily, days)
        except Exception as e:  # noqa: BLE001 - the page shows the error instead of hanging
            logger.error(f"usage page: {e}")
            return JSONResponse({"error": "usage statistics are unavailable"}, status_code=500)
        return JSONResponse(data)

    app.add_route("/web/usage", web_usage, methods=["GET"])
    app.add_route("/api/usage", api_usage, methods=["GET"])
