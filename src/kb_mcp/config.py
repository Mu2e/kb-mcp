"""
Configuration management for kb-mcp.
"""

import os
from typing import Optional
import json

from anyio import Path
from dotenv import load_dotenv, find_dotenv

# Load shared configuration from .env
# override=True ensures .env values take precedence over shell environment
# variables. This prevents issues where a shell-level DEFAULT_LLM_MODEL
# (e.g., set for Claude Desktop) overrides the backend model settings
# needed for OpenAI API calls (summarization, graph extraction, etc.).
load_dotenv(override=True)

# Load user-specific overrides from .env.local (takes precedence over .env)
# This allows users to override settings (e.g., ALCF credentials) without
# modifying the shared .env file (which may be a symlink on NERSC)
env_path = find_dotenv()
local_env_path = Path(env_path).with_name(".env.local") if env_path else None
if local_env_path:
    load_dotenv(dotenv_path=local_env_path, override=True)


def get_env_local_path() -> Optional[str]:
    """Path to the user-specific `.env.local` override file, if resolvable."""
    return str(local_env_path) if local_env_path else None

# --- Helpers ---

def _get_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() == "true"

def _get_bool_or_none(key: str):
    """Like _get_bool, but distinguishes "unset" from "set to false".

    Needed for settings that override a broader one only when explicitly
    given - e.g. WEB_REQUIRE_AUTH overriding DISABLE_AUTH.
    """
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return None
    return val.strip().lower() == "true"

def _get_str(key: str, default):
    """Like os.getenv, but treats an empty value as unset.

    `.env` files (including our own .env.example) commonly carry placeholder
    lines such as `AGENT_MODEL=`, meaning "use the default". os.getenv only
    applies its default when the variable is absent, so those lines silently
    produced "" instead - e.g. the chat page showed an empty model name, and
    CHUNK_STRATEGY resolved to "" rather than "tokens". _get_int already
    behaved this way; this brings string settings in line.
    """
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return val

def _get_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    # Kubernetes injects service vars like DB_PORT=tcp://10.x.x.x:5432; extract just the port
    if val.startswith("tcp://") and ":" in val:
        val = val.rsplit(":", 1)[-1]
    return int(val)

# --- Configuration Getters ---

# Database

def get_database_config() -> dict:
    """All database settings.

    Returns:
        dict: Database configuration with keys:

            * `host` (str): Database host (Env: `DB_HOST`, default: 'localhost').
            * `port` (int): Database port (Env: `DB_PORT`, default: 5432).
            * `name` (str): Database name (Env: `DB_NAME`, default: 'kb_mcp').
            * `user` (str): Database user (Env: `DB_USER`).
            * `password` (str): Database password (Env: `DB_PASSWORD`).
            * `schema` (str): Database schema (Env: `DB_SCHEMA`, default: 'public').
            * `sqlite_path` (str): Path to SQLite DB (Env: `SQLITE_DB_PATH`, default: 'data/kb.db').
    """
    return {
        'host': _get_str("DB_HOST", "localhost"),
        'port': _get_int("DB_PORT", 5432),
        'name': _get_str("DB_NAME", "kb_mcp"),
        'user': os.getenv("DB_USER"),
        'password': os.getenv("DB_PASSWORD"),
        'schema': _get_str("DB_SCHEMA", "public"),
        'sqlite_path': _get_str("SQLITE_DB_PATH", "data/kb.db"),
    }

# Server
def get_server_config() -> dict:
    """All server settings.

    Returns:
        dict: Server configuration with keys:

            * `base_url` (str): Server base URL (Env: `BASE_URL`, default: 'https://127.0.0.1').
            * `port` (int): Server port (Env: `PORT`, default: 8443).
            * `host` (str): Server host (Env: `SERVER_HOST`, default: '127.0.0.1').
            * `use_https` (bool): Whether HTTPS is enabled (Env: `USE_HTTPS`, default: True).
            * `log_level` (str): Logging level (Env: `LOG_LEVEL`, default: 'INFO').
            * `mcp_log_level` (str): App-specific log level (Env: `MCP_LOG_LEVEL`).
            * `audit_log_file` (str): Path to audit log (Env: `AUDIT_LOG_FILE`).
            * `max_upload_size` (int): Max upload bytes (Env: `MAX_UPLOAD_SIZE`, default: 100MB).
            * `use_firestore` (bool): Use Firestore for session storage (Env: `SESSION_STORE_FIRESTORE`, default: False).
            * `site_name` (str): Display name for the web UI (Env: `SITE_NAME`, default: 'Knowledge Base').
            * `hide_graph` (bool): Hide the knowledge graph from the web UI and MCP tools (Env: `HIDE_GRAPH`, default: False).
            * `mcp_host` (str): Bind address for the MCP server (Env: `MCP_HOST`, falls back to `SERVER_HOST`, default: '127.0.0.1').
            * `web_host` (str): Bind address for the web UI server (Env: `WEB_HOST`, default: '127.0.0.1', i.e. loopback only).
            * `web_port` (int): Port for the web UI server (Env: `WEB_PORT`, default: `PORT` + 1).
    """
    log_level = _get_str("LOG_LEVEL", "INFO").upper()
    return {
        'base_url': _get_str("BASE_URL", "https://127.0.0.1"),
        'port': _get_int("PORT", 8443),
        'host': _get_str("SERVER_HOST", "127.0.0.1"),
        'use_https': _get_bool("USE_HTTPS", True),
        'log_level': log_level,
        'mcp_log_level': _get_str("MCP_LOG_LEVEL", log_level).upper(),
        'audit_log_file': os.getenv("AUDIT_LOG_FILE", ""),
        'max_upload_size': _get_int("MAX_UPLOAD_SIZE", 104857600),
        'use_firestore': _get_bool("SESSION_STORE_FIRESTORE", False),
        'site_name': _get_str("SITE_NAME", "Knowledge Base"),
        'hide_graph': _get_bool("HIDE_GRAPH", False),
        # The MCP endpoint and the web UI are served by two separate uvicorn
        # servers so they can have different exposure: MCP is reachable from
        # the network and gated on an API key, while the web UI binds to
        # loopback by default. See kb_mcp.server.server.
        'mcp_host': _get_str("MCP_HOST", _get_str("SERVER_HOST", "127.0.0.1")),
        'web_host': _get_str("WEB_HOST", "127.0.0.1"),
        'web_port': _get_int("WEB_PORT", _get_int("PORT", 8443) + 1),
    }

# LLM
def get_default_llm_model() -> str:
    """Default LLM model used as fallback when specific model settings are not set.
    
    **Env Variable:** `DEFAULT_LLM_MODEL` (default: `gemini-2.5-flash-lite`).
    This is used as a fallback when specific model settings (e.g., SUMMARY_MODEL) are not set.
    """
    return _get_str("DEFAULT_LLM_MODEL", "gemini-2.5-flash-lite")

def get_llm_config() -> dict:
    """All LLM settings.

    Returns:
        dict: LLM configuration with keys:

            * `openai_api_key` (str|None): API Key (Env: `OPENAI_API_KEY`).
            * `openai_base_url` (str|None): Custom API base URL (Env: `OPENAI_BASE_URL`).
            * `openai_api_key_models` (dict): Per-model API key overrides (Env: `OPENAI_API_KEY_MODELS`,
              JSON object mapping model name -> key). Needed when models are routed to different
              endpoints via `OPENAI_BASE_URL_MODELS`: without it, one endpoint's credential is sent
              to all of them.
            * `default_model` (str): Default model fallback (Env: `DEFAULT_LLM_MODEL`).
            * `summary_model` (str): Summarization model (Env: `SUMMARY_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `eval_gen_model` (str): Evaluation question generation model (Env: `EVAL_GEN_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `eval_judge_model` (str): Evaluation answer judging model (Env: `EVAL_JUDGE_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `image_description_model` (str): Model for image descriptions (Env: `PARSE_IMAGE_DESCRIPTION_MODEL`, defaults to `DEFAULT_IMAGE_DESCRIPTION_MODEL`). Must be vision-capable and served by the endpoint it routes to — see `OPENAI_BASE_URL_MODELS`.
            * `graph_relation_extraction_model` (str): Graph relation extraction model (Env: `GRAPH_EXTRACTION_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `parser_comparison_model` (str): Parser comparison model (Env: `PARSER_COMP_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `privacy_filter_model` (str): Privacy classification model (Env: `PRIVACY_FILTER_MODEL`, defaults to DEFAULT_LLM_MODEL).
    """
    base_url_models = json.loads(_get_str("OPENAI_BASE_URL_MODELS", "{}"))
    api_key_models = json.loads(_get_str("OPENAI_API_KEY_MODELS", "{}"))
    return {
        'openai_api_key': os.getenv("OPENAI_API_KEY"),
        'openai_base_url': os.getenv("OPENAI_BASE_URL"),
        'openai_base_url_models': base_url_models,
        'openai_api_key_models': api_key_models,
        'default_model': get_default_llm_model(),
        'summary_model': _get_str("SUMMARY_MODEL", get_default_llm_model()),
        'eval_gen_model': _get_str("EVAL_GEN_MODEL", get_default_llm_model()),
        'eval_judge_model': _get_str("EVAL_JUDGE_MODEL", get_default_llm_model()),
        # Vision fallback, NOT the default LLM: text-only models silently
        # refuse images, so unset means a vision-capable default.
        'image_description_model': _get_str("PARSE_IMAGE_DESCRIPTION_MODEL", DEFAULT_IMAGE_DESCRIPTION_MODEL),
        'graph_relation_extraction_model': _get_str("GRAPH_EXTRACTION_MODEL", get_default_llm_model()),
        'parser_comparison_model': _get_str("PARSER_COMP_MODEL", get_default_llm_model()),
        'privacy_filter_model': _get_str("PRIVACY_FILTER_MODEL", get_default_llm_model()),
    }

# Graph
def get_graph_config() -> dict:
    """Graph knowledge base settings.

    Returns:
        dict: Graph configuration with keys:

            * `node_similarity_threshold` (float): Minimum similarity for vector matching (Env: `GRAPH_NODE_SIMILARITY_THRESHOLD`, default: 0.85).
            * `embedding` (dict): Embedding configuration (see `get_embedding_config()`).
            * `graph_relation_extraction_model` (str): Graph relation extraction model (Env: `GRAPH_EXTRACTION_MODEL`, defaults to DEFAULT_LLM_MODEL).
    """
    return {
        'node_similarity_threshold': float(_get_str("GRAPH_NODE_SIMILARITY_THRESHOLD", "0.85")),
        'embedding': get_embedding_config(),
        'graph_relation_extraction_model': _get_str("GRAPH_EXTRACTION_MODEL", get_default_llm_model()),
        'domain': _get_str("GRAPH_DOMAIN", None),  # e.g., "mu2e" for Mu2e-specific extraction
    }

# Integrations
def get_github_oauth_config() -> dict:
    """GitHub OAuth settings.

    Returns:
        dict: GitHub configuration with keys:

            * `client_id` (str): OAuth Client ID (Env: `GITHUB_CLIENT_ID`).
            * `client_secret` (str): OAuth Client Secret (Env: `GITHUB_CLIENT_SECRET`).
            * `required_repo` (str): Required repository access (Env: `GITHUB_REQUIRED_REPO`).
    """
    return {
        'client_id': os.getenv("GITHUB_CLIENT_ID", ""),
        'client_secret': os.getenv("GITHUB_CLIENT_SECRET", ""),
        'required_repo': os.getenv("GITHUB_REQUIRED_REPO", ""),
    }

def get_globus_oauth_config() -> dict:
    """Globus OAuth settings.

    Returns:
        dict: Globus configuration with keys:

            * `client_id` (str): OAuth Client ID (Env: `GLOBUS_CLIENT_ID`).
            * `client_secret` (str): OAuth Client Secret (Env: `GLOBUS_CLIENT_SECRET`).
            * `required_group` (str): Required Globus group (Env: `GLOBUS_REQUIRED_GROUP`).
    """
    return {
        'client_id': os.getenv("GLOBUS_CLIENT_ID", ""),
        'client_secret': os.getenv("GLOBUS_CLIENT_SECRET", ""),
        'required_group': os.getenv("GLOBUS_REQUIRED_GROUP", ""),
    }

def get_auth_config() -> dict:
    """Authentication settings (OAuth, sessions, API keys).

    Returns:
        dict: Authentication configuration with keys:

            * `disable_auth` (bool): Security override (Env: `DISABLE_AUTH`, default: False).
            * `session_timeout` (int): Web session timeout in seconds (Env: `WEB_SESSION_TIMEOUT`, default: 86400).
            * `reverify_interval` (int): Session re-verification interval in seconds (Env: `WEB_REVERIFY_INTERVAL`, default: 3600).
            * `use_firestore` (bool): Use Firestore for session storage (Env: `SESSION_STORE_FIRESTORE`, default: False).
            * `authorization_code_timeout` (int): MCP authorization code expiration in seconds (Env: `OAUTH_AUTHORIZATION_CODE_TIMEOUT`, default: 600).
            * `oauth_state_timeout` (int): Web OAuth state expiration in seconds (Env: `OAUTH_STATE_TIMEOUT`, default: 600).
            * `access_token_timeout` (int): MCP access token expiration in seconds (Env: `OAUTH_ACCESS_TOKEN_TIMEOUT`, default: 3600).
            * `github` (dict): GitHub OAuth configuration.
            * `globus` (dict): Globus OAuth configuration.
            * `oauth_provider` (str): OAuth provider name (default: None).
            * `web_public_mode` (bool): Serve the browsable pages without a session,
              keeping only the administrative pages behind the admin password
              (Env: `WEB_PUBLIC_MODE`, default: False).
            * `admin_password` (str): Plaintext admin password for the web UI's
              write/administrative pages (Env: `ADMIN_PASSWORD`, default: '' = unset).
            * `admin_password_hash` (str): sha256 hex digest of the admin password,
              used in preference to `admin_password` when set (Env: `ADMIN_PASSWORD_HASH`).
            * `web_require_auth` (bool): Whether the web UI requires login
              (Env: `WEB_REQUIRE_AUTH`; falls back to the inverse of `DISABLE_AUTH`).
            * `mcp_require_api_key` (bool): Whether the MCP endpoint requires an API
              key or OAuth token (Env: `MCP_REQUIRE_API_KEY`; falls back to the
              inverse of `DISABLE_AUTH`).
    """
    data = {
        'disable_auth': _get_bool("DISABLE_AUTH", False),
        'session_timeout': _get_int("WEB_SESSION_TIMEOUT", 86400),
        'reverify_interval': _get_int("WEB_REVERIFY_INTERVAL", 3600),
        'use_firestore': _get_bool("SESSION_STORE_FIRESTORE", False),
        'authorization_code_timeout': _get_int("OAUTH_AUTHORIZATION_CODE_TIMEOUT", 600),
        'oauth_state_timeout': _get_int("OAUTH_STATE_TIMEOUT", 600),
        'access_token_timeout': _get_int("OAUTH_ACCESS_TOKEN_TIMEOUT", 3600),
        'github': get_github_oauth_config(),
        'globus': get_globus_oauth_config(),
    }

    globus_enabled = data['globus']['client_id'] and data['globus']['client_secret']
    github_enabled = data['github']['client_id'] and data['github']['client_secret']

    if globus_enabled and github_enabled:
        raise ValueError(
        "Both GitHub and Globus OAuth are configured. "
        "Only one OAuth provider can be enabled at a time. "
        "Please set only GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET OR GLOBUS_CLIENT_ID/GLOBUS_CLIENT_SECRET."
    )

    if globus_enabled:
        data['oauth_provider'] = 'globus'
    elif github_enabled:
        data['oauth_provider'] = 'github'
    else:
        data['oauth_provider'] = None

    # Per-surface auth. DISABLE_AUTH remains the blanket switch; WEB_REQUIRE_AUTH
    # and MCP_REQUIRE_API_KEY override it for one surface when explicitly set.
    # This matters because the two are served on separate sockets with very
    # different exposure: the web UI binds to loopback, while MCP is typically
    # reachable from the network and so should stay gated by default.
    # Admin password for the write/administrative pages of the web UI. Plain
    # ADMIN_PASSWORD is the simple option; ADMIN_PASSWORD_HASH (sha256 hex)
    # takes precedence when both are set, so a deployment can avoid keeping the
    # password in plaintext without any code change.
    data['admin_password'] = os.getenv("ADMIN_PASSWORD", "")
    data['admin_password_hash'] = os.getenv("ADMIN_PASSWORD_HASH", "")

    # Public mode: the browsable pages (documents, chat, graph, and the API
    # reads they depend on) are served without any session at all, while the
    # administrative pages stay behind the admin password. Off by default, so
    # existing deployments keep requiring a session for everything.
    data['web_public_mode'] = _get_bool("WEB_PUBLIC_MODE", False)

    web_override = _get_bool_or_none("WEB_REQUIRE_AUTH")
    data['web_require_auth'] = (
        web_override if web_override is not None else not data['disable_auth']
    )

    mcp_override = _get_bool_or_none("MCP_REQUIRE_API_KEY")
    data['mcp_require_api_key'] = (
        mcp_override if mcp_override is not None else not data['disable_auth']
    )

    return data

# Processing
def get_batch_config() -> dict:
    """Batch processing settings for parallel operations.

    Returns:
        dict: Batch configuration with keys:

            * `parse_batch_size` (int): Batch size for parse_all (Env: `PARSE_BATCH_SIZE`, default: 10).
            * `chunk_batch_size` (int): Batch size for chunk_and_embed_all (Env: `CHUNK_BATCH_SIZE`, default: 10).
            * `extract_batch_size` (int): Batch size for graph extract_all (Env: `EXTRACT_BATCH_SIZE`, default: 10).
    """
    return {
        'parse_batch_size': _get_int("PARSE_BATCH_SIZE", 10),
        'chunk_batch_size': _get_int("CHUNK_BATCH_SIZE", 10),
        'extract_batch_size': _get_int("EXTRACT_BATCH_SIZE", 10),
    }

# Vision-capable model used for figure / page-image descriptions when the
# user hasn't set PARSE_IMAGE_DESCRIPTION_MODEL explicitly. Falling back to
# DEFAULT_LLM_MODEL is unsafe — the project default `openai/gpt-oss-120b`
# is text-only and silently refuses images.
#
# Qwen3.6 won a head-to-head probe on a real Mu2e plot: reads axis labels
# accurately, follows the "no specific values" instruction, ~2.6 s per
# call. NVIDIA NVILA-2-lite (the previous default) confidently invented
# axis ranges and data values on the same probe — a non-starter for
# physics-figure retrieval where hallucinated specifics get embedded
# into search.
# Fallback when PARSE_IMAGE_DESCRIPTION_MODEL is unset. There is no model
# name that is correct for every deployment, so treat this as a placeholder,
# not a guarantee: whichever model you use must (a) be served by the endpoint
# it is routed to and (b) actually support vision. Both are checked at parse
# time by parser/image_descriptions.py, which refuses to run and names the
# models the endpoint does serve rather than filling the document with
# "Image description unavailable" placeholders.
DEFAULT_IMAGE_DESCRIPTION_MODEL = "qwen/qwen3.6"


def get_parser_config() -> dict:
    """Parser settings.

    Returns:
        dict: Parser configuration with keys:

            * `parser` (str): Parser framework to use (Env: `KB_PARSER`, default: 'kb-mcp').
            * `image_additional_doc` (bool): Create separate docs for images (Env: `PARSE_IMAGE_ADDITIONAL_DOC`).
            * `image_llm_description` (bool): Use LLM for image descriptions (Env: `PARSE_IMAGE_LLM_DESCRIPTION`).
            * `image_description_model` (str): Model for descriptions (Env: `PARSE_IMAGE_DESCRIPTION_MODEL`, defaults to `DEFAULT_IMAGE_DESCRIPTION_MODEL`). Must be vision-capable and served by the endpoint it routes to — see `OPENAI_BASE_URL_MODELS`.
            * `image_description_num_workers` (int): Parallel worker count (Env: `PARSE_IMAGE_DESCRIPTION_NUMWORKERS`, default: 6).
            * `marker_output_base` (str): Base directory for pre-existing Marker output (Env: `MARKER_OUTPUT_BASE`, default: 'data/sources/sld-scanned/extracted_output').
            * `ocr` (bool): Run OCR during Docling PDF parsing (Env: `PARSE_OCR`, default: True). Required for scanned documents (e.g. SLD scans); born-digital-only sweeps can disable it for speed.
            * `table_llm_summary` (bool): Generate LLM summaries for table records (Env: `PARSE_TABLE_LLM_SUMMARY`, default: False).
            * `table_summary_model` (str): Model for table summaries (Env: `PARSE_TABLE_SUMMARY_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `table_summary_num_workers` (int): Parallel worker count (Env: `PARSE_TABLE_SUMMARY_NUMWORKERS`, default: 6).
    """
    return {
        'parser': _get_str("KB_PARSER", "kb-mcp"),
        'image_additional_doc': _get_bool("PARSE_IMAGE_ADDITIONAL_DOC", True),
        'image_llm_description': _get_bool("PARSE_IMAGE_LLM_DESCRIPTION", True),
        'image_description_model': _get_str("PARSE_IMAGE_DESCRIPTION_MODEL", DEFAULT_IMAGE_DESCRIPTION_MODEL),
        'image_description_num_workers': _get_int("PARSE_IMAGE_DESCRIPTION_NUMWORKERS", 6),
        'marker_output_base': _get_str("MARKER_OUTPUT_BASE", "data/sources/sld-scanned/extracted_output"),
        # OCR on by default: scanned-document pipelines depend on it.
        # Born-digital-only sweeps can disable it per-deployment for speed.
        'ocr': _get_bool("PARSE_OCR", True),
        'table_llm_summary': _get_bool("PARSE_TABLE_LLM_SUMMARY", False),
        'table_summary_model': _get_str("PARSE_TABLE_SUMMARY_MODEL", get_default_llm_model()),
        'table_summary_num_workers': _get_int("PARSE_TABLE_SUMMARY_NUMWORKERS", 6),
        # Docling CodeFormulaV2-based formula enrichment on PDF parses.
        # Recovers equations the layout model would otherwise leave as
        # <!-- formula-not-decoded --> stubs, rendering them as `$$...$$`
        # LaTeX blocks. Heavy: cold start downloads ~hundreds of MB of
        # model weights and adds significant per-page parse time. Default off.
        'formula_enrichment': _get_bool("PARSE_FORMULA_ENRICHMENT", False),
        # Per-document auto-decide. When true, runs a cheap PyPDF2 pre-scan
        # to score the PDF's math-character density; if score >= threshold,
        # enables formula enrichment for that single document. Auto wins
        # over the manual flag when both are set (because auto's
        # off-decision is the careful one — "no math on this doc").
        'formula_enrichment_auto': _get_bool("PARSE_FORMULA_ENRICHMENT_AUTO", False),
        'formula_enrichment_auto_threshold': float(
            _get_str("PARSE_FORMULA_ENRICHMENT_AUTO_THRESHOLD", "0.0005")
        ),
    }

def get_embedding_config() -> dict:
    """Embedding settings.

    Returns:
        dict: Embedding configuration with keys:

            * `provider` (str): Provider name (Env: `EMBEDDING_PROVIDER`, default: 'st').
            * `model` (str|None): Specific model name (Env: `EMBEDDING_MODEL`).
            * `chunk_strategy` (str): Chunking method (Env: `CHUNK_STRATEGY`,
              default: 'tokens'). `"section"` routes text documents whose
              `parser_output` holds a DoclingDocument payload through the
              structure-aware walker (`chunk_from_docling_json`), which emits
              one chunk per section (splitting only oversized sections) with
              page_start/page_end/body_self_refs/section_path populated —
              rather than plain token windows.
            * `chunk_size` (int|None): Target tokens per chunk for the
              `tokens` strategy (Env: `CHUNK_SIZE`). None means unset, and the
              chunker sizes itself to the embedding model's window instead of
              guessing — see `kb.embedding.budget.token_chunk_size`. Ignored
              by `section`, which always sizes itself from the window.
            * `chunk_overlap` (int|None): Tokens of overlap between adjacent
              token chunks (Env: `CHUNK_OVERLAP`). Unset means 10% of
              `chunk_size`; the chunker clamps it to half of `chunk_size`.

    Both are None when unset rather than carrying a coded default, so the
    embedding layer can tell "the operator chose this" from "nobody said",
    and only derive a window-sized value in the second case.
    """
    chunk_size = os.getenv("CHUNK_SIZE")
    chunk_overlap = os.getenv("CHUNK_OVERLAP")
    return {
        'provider': _get_str("EMBEDDING_PROVIDER", "st"),
        'model': os.getenv("EMBEDDING_MODEL"),
        'chunk_strategy': _get_str("CHUNK_STRATEGY", "tokens"),
        # Empty means unset in this codebase, so `CHUNK_SIZE=` must not read
        # as an explicit 0.
        'chunk_size': int(chunk_size) if chunk_size else None,
        'chunk_overlap': int(chunk_overlap) if chunk_overlap else None,
    }

def get_eval_config() -> dict:
    """Eval settings.

    Returns:
        dict: Evaluation configuration with keys:

            * `gen_model` (str): Question generation model (Env: `EVAL_GEN_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `judge_model` (str): Answer judging model (Env: `EVAL_JUDGE_MODEL`, defaults to DEFAULT_LLM_MODEL).
    """
    return {
        'gen_model': _get_str("EVAL_GEN_MODEL", get_default_llm_model()),
        'judge_model': _get_str("EVAL_JUDGE_MODEL", get_default_llm_model()),
    }

def get_search_config() -> dict:
    """Search settings.

    Returns:
        dict: Search configuration with keys:

            * `max_chunks_per_doc` (int): Maximum chunks per document in search results (Env: `SEARCH_MAX_CHUNKS_PER_DOC`, default: 10).
            * `initial_limit_multiplier` (int): Multiplier for initial chunk retrieval (Env: `SEARCH_INITIAL_LIMIT_MULTIPLIER`, default: 50).
            * `rrf_k` (int): Reciprocal Rank Fusion constant (Env: `SEARCH_RRF_K`, default: 60).
    """
    return {
        'max_chunks_per_doc': _get_int("SEARCH_MAX_CHUNKS_PER_DOC", 10),
        'initial_limit_multiplier': _get_int("SEARCH_INITIAL_LIMIT_MULTIPLIER", 50),
        'rrf_k': _get_int("SEARCH_RRF_K", 60),
        'router_enabled': _get_bool("ROUTER_ENABLED", False),
    }

def get_reranker_config() -> dict:
    """Reranker configuration.

    Returns:
        dict: Reranker configuration with keys:

            * `enabled` (bool): Whether reranking is enabled (Env: `RERANKER_ENABLED`, default: false).
            * `model_name` (str): Cross-encoder model name (Env: `RERANKER_MODEL`, default: cross-encoder/ms-marco-MiniLM-L-6-v2).
    """
    return {
        'enabled': _get_bool("RERANKER_ENABLED", False),
        'model_name': _get_str("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
    }

def get_agent_config() -> dict:
    """Research agent configuration.

    Returns:
        dict: Agent configuration with keys:

            * `agent_model` (str): LLM model for agent reasoning (Env: `AGENT_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `max_depth` (int): Maximum recursion depth for agent delegation (Env: `AGENT_MAX_DEPTH`, default: 2).
    """
    return {
        'agent_model': _get_str("AGENT_MODEL", get_default_llm_model()),
        'max_depth': _get_int("AGENT_MAX_DEPTH", 2),
        'max_tool_output_chars': _get_int("AGENT_MAX_TOOL_OUTPUT_CHARS", 30000),
        'max_aggregated_tool_output_chars': _get_int("AGENT_MAX_AGGREGATED_TOOL_OUTPUT_CHARS", 100000),
    }

# Paths
def get_data_dir() -> str:
    """Data directory. **Env Variable:** `DATA_DIR` (default: `data`)."""
    return _get_str("DATA_DIR", "data")

def get_api_keys_file() -> str:
    """API keys file path. **Env Variable:** `API_KEYS_FILE`."""
    return _get_str("API_KEYS_FILE", f"{get_data_dir()}/api_keys.json")

def get_all_config() -> dict:
    """Get all configuration (sanitized for logging).

    Returns:
        dict: A nested dictionary containing all configuration groups
              (database, server, llm, etc.) with secrets redacted.
    """
    return {
        'database': get_database_config(),
        'server': get_server_config(),
        'llm': {
            **get_llm_config(),
            'openai_api_key': '***',
            # Same secrets, one level down — redact the values, keep the
            # model names so the routing stays inspectable.
            'openai_api_key_models': {k: '***' for k in get_llm_config()['openai_api_key_models']},
        },
        'github': {**get_github_oauth_config(), 'client_secret': '***'},
        'globus': {**get_globus_oauth_config(), 'client_secret': '***'},
        'auth': {k: v for k, v in get_auth_config().items() if k not in ['github', 'globus', 'oauth_provider']},
        'parser': get_parser_config(),
        'embedding': get_embedding_config(),
        'eval': get_eval_config(),
        'paths': {'data_dir': get_data_dir(), 'api_keys_file': get_api_keys_file()}
    }
