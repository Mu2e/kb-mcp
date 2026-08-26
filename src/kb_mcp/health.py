"""Connectivity checks for the endpoints the pipeline depends on.

The failure this exists for: a wrong model name or a dead endpoint doesn't
stop an import. `generate_image_descriptions()` catches every exception per
image and writes "Image description unavailable"; summarisation and graph
extraction degrade similarly. A run finishes "successfully" with hollow
documents in it, and the only trace is a log line nobody reads.

So the checks here do a *real round-trip* against every configured target,
before any work is done. In particular they do not use `/v1/models`: the ALCF
endpoint serves chat completions perfectly well while answering `/v1/models`
with a 404, so a model listing is neither necessary nor sufficient evidence
that inference works.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from .config import (
    get_agent_config,
    get_database_config,
    get_embedding_config,
    get_eval_config,
    get_llm_config,
    get_parser_config,
)

# 8x8 solid red PNG. Small enough to inline, big enough for any vision model
# to answer "what colour is this?" — the cheapest way to tell a vision model
# from a text-only one that politely refuses at HTTP 200.
# Public: image_descriptions.py runs the same probe as its parse-time preflight.
RED_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR42mP4z8CAFTEMLQkAKP8"
    "/wc53yE8AAAAASUVORK5CYII="
)
RED_WORDS = ("red", "crimson", "scarlet", "maroon", "vermilion")

# Import sources are reachability-only: we check the host answers HTTP, not
# that credentials work, because logging in has side effects (SSO session).
_SOURCE_ENDPOINTS = (
    ("docdb", "https://mu2e-docdb.fnal.gov/cgi-bin/sso/", ("MU2E_DOCDB_USERNAME", "MU2E_DOCDB_PASSWORD")),
    ("wiki", "https://mu2ewiki.fnal.gov", ()),
)

DEFAULT_TIMEOUT = 30.0


@dataclass
class CheckResult:
    """Outcome of a single connectivity check."""

    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False
    seconds: float = 0.0

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "OK" if self.ok else "FAIL"


def _timed(name: str, fn: Callable[[], str]) -> CheckResult:
    """Run `fn`, turning its return value into a detail string and any
    exception into a failure. Every check goes through here so one broken
    target can't abort the rest of the report."""
    start = time.monotonic()
    try:
        detail = fn()
        return CheckResult(name, True, detail, seconds=time.monotonic() - start)
    except Exception as e:
        return CheckResult(
            name, False, f"{type(e).__name__}: {e}", seconds=time.monotonic() - start
        )


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


def check_database() -> CheckResult:
    """SELECT 1 through the configured engine."""
    db = get_database_config()
    target = f"{db['user']}@{db['host']}:{db['port']}/{db['name']}"

    def run() -> str:
        from sqlalchemy import text

        from .kb.database import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1")).scalar()
        return target

    result = _timed("database", run)
    if not result.ok:
        result.detail = f"{target} — {result.detail}"
    return result


def check_embeddings(timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    """Embed one short string with the configured embedder.

    Local sentence-transformers models are skipped: there is no endpoint to
    check, and a cold load pulls hundreds of MB just to say "yes, torch works".
    """
    cfg = get_embedding_config()
    provider = (cfg["provider"] or "").lower()
    model = cfg["model"] or "<provider default>"

    if provider != "openai":
        return CheckResult(
            "embeddings",
            True,
            f"provider={provider or '<unset>'} model={model} — local model, no endpoint to check",
            skipped=True,
        )

    def run() -> str:
        from .kb.embedding.utils import get_embedder

        embedder = get_embedder()
        vectors = embedder(["connection check"])
        dim = len(vectors[0]) if vectors else 0
        return f"provider={provider} model={model} dim={dim}"

    return _timed("embeddings", run)


def _resolve_base_url(model: str, llm_config: dict) -> str:
    return llm_config["openai_base_url_models"].get(model, llm_config["openai_base_url"]) or ""


def llm_targets() -> List[Tuple[str, str, List[str]]]:
    """Distinct (model, base_url) pairs the pipeline will actually call.

    Several stages usually share one model; probing per stage would hammer the
    same endpoint five times. Group by the pair and report which stages ride
    on it, so a failure names everything it breaks.
    """
    llm_config = get_llm_config()
    parser_config = get_parser_config()
    eval_config = get_eval_config()
    agent_config = get_agent_config()

    stages = [
        ("default", llm_config["default_model"]),
        ("summary", llm_config["summary_model"]),
        ("image-description", parser_config["image_description_model"]),
        ("graph-extraction", llm_config["graph_relation_extraction_model"]),
        ("privacy-filter", llm_config["privacy_filter_model"]),
        ("agent", agent_config["agent_model"]),
        ("eval-gen", eval_config["gen_model"]),
        ("eval-judge", eval_config["judge_model"]),
    ]
    if parser_config.get("table_llm_summary"):
        stages.append(("table-summary", parser_config["table_summary_model"]))

    grouped: Dict[Tuple[str, str], List[str]] = {}
    for stage, model in stages:
        if not model:
            continue
        key = (model, _resolve_base_url(model, llm_config))
        grouped.setdefault(key, []).append(stage)

    return [(model, base_url, names) for (model, base_url), names in grouped.items()]


def _client(model: str, timeout: float):
    from .llm.llm import get_openai_client

    # The SDK default is a 600 s timeout with retries — fine for a real
    # request, useless for a health check that should say "dead" quickly.
    return get_openai_client(model=model).with_options(timeout=timeout, max_retries=0)


def check_llm(model: str, base_url: str, stages: List[str],
              timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    """One minimal chat completion — the only honest liveness test.

    `base_url` is for the report only; the client itself is built by
    `get_openai_client()`, which applies exactly the routing the pipeline uses.
    Pass what `llm_targets()` resolved so the two can't drift.
    """
    where = base_url or "<openai default>"
    label = f"llm[{model}]"

    def run() -> str:
        client = _client(model, timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the word OK."}],
            max_tokens=32,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        content = (response.choices[0].message.content or "").strip()
        # An empty body still proves the endpoint served the model — reasoning
        # models spend the whole budget on a thinking trace. Report it as-is.
        reply = content.replace("\n", " ")[:40] or "<empty reply>"
        return f"{where} ({', '.join(stages)}) -> {reply}"

    result = _timed(label, run)
    if not result.ok:
        result.detail = f"{where} ({', '.join(stages)}) — {result.detail}"
    return result


def check_vision(model: str, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    """Send a solid red square and ask what colour it is.

    A text-only model accepts `image_url` content and answers at HTTP 200 with
    a refusal — silent garbage that a plain chat probe would call healthy. The
    colour question is the cheapest thing that separates the two.
    """
    where = base_url or "<openai default>"
    label = f"vision[{model}]"

    def run() -> str:
        client = _client(model, timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What color is this image? Answer with one word."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{RED_PNG_B64}"},
                        },
                    ],
                }
            ],
            max_tokens=64,
            # Same reason as image_descriptions.py: Qwen3.x reasons by default
            # and burns the budget on a thinking trace. Ignored elsewhere.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("model returned empty content — vision likely unsupported")
        reply = content.replace("\n", " ")[:80]
        if not any(word in content.lower() for word in RED_WORDS):
            raise ValueError(
                f"could not identify the test image (answered {reply!r}, expected 'red') — "
                f"model is probably text-only; set PARSE_IMAGE_DESCRIPTION_MODEL to a "
                f"vision-capable model and route it with OPENAI_BASE_URL_MODELS"
            )
        return f"{where} -> {reply}"

    result = _timed(label, run)
    if not result.ok:
        result.detail = f"{where} — {result.detail}"
    return result


def check_source(name: str, url: str, env_vars: Tuple[str, ...],
                 timeout: float = 15.0) -> CheckResult:
    """Reachability of an import source.

    Any 2xx/3xx/4xx counts as up — a 303 to SSO or a 401 both prove the host is
    answering, and we deliberately don't have a session to do better than that.
    5xx does not: the host is there but the application behind it is broken, so
    an import would fail anyway. Credentials are reported as present or absent,
    never exercised, because logging in creates an SSO session.
    """

    def run() -> str:
        import requests

        response = requests.get(url, timeout=timeout, allow_redirects=False)
        missing = [v for v in env_vars if not os.getenv(v)]
        creds = f" — missing {', '.join(missing)}" if missing else ""
        if response.status_code >= 500:
            raise RuntimeError(f"HTTP {response.status_code} — server error{creds}")
        return f"{url} HTTP {response.status_code}{creds}"

    result = _timed(f"source[{name}]", run)
    if not result.ok:
        result.detail = f"{url} — {result.detail}"
    return result


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def check_connections(
    timeout: float = DEFAULT_TIMEOUT,
    include_sources: bool = True,
    include_vision: bool = True,
) -> List[CheckResult]:
    """Run every check and return the results in report order."""
    results: List[CheckResult] = [check_database(), check_embeddings(timeout=timeout)]

    llm_config = get_llm_config()
    if not llm_config["openai_api_key"]:
        results.append(
            CheckResult("llm", False, "OPENAI_API_KEY is not set — no LLM stage can run")
        )
    else:
        vision_model = get_parser_config()["image_description_model"]
        for model, base_url, stages in llm_targets():
            results.append(check_llm(model, base_url, stages, timeout=timeout))
            if include_vision and model == vision_model:
                results.append(check_vision(model, base_url, timeout=timeout))

    if include_sources:
        for name, url, env_vars in _SOURCE_ENDPOINTS:
            results.append(check_source(name, url, env_vars))

    return results


def format_report(results: List[CheckResult]) -> str:
    """Render results as an aligned, greppable block."""
    width = max((len(r.name) for r in results), default=0)
    lines = ["Connection checks", "=" * 60]
    for r in results:
        lines.append(f"  {r.status:<4} {r.name:<{width}}  {r.detail} [{r.seconds:.1f}s]")

    failed = [r for r in results if not r.ok and not r.skipped]
    lines.append("=" * 60)
    if failed:
        lines.append(f"{len(failed)} of {len(results)} checks failed: "
                     f"{', '.join(r.name for r in failed)}")
    else:
        lines.append(f"All {len(results)} checks passed.")
    return "\n".join(lines)


def run_and_report(
    timeout: float = DEFAULT_TIMEOUT,
    include_sources: bool = True,
    include_vision: bool = True,
) -> int:
    """Check everything, print the report, return a process exit code."""
    results = check_connections(
        timeout=timeout, include_sources=include_sources, include_vision=include_vision
    )
    print(format_report(results))
    return 1 if any(not r.ok and not r.skipped for r in results) else 0
