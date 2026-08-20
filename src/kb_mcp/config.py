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
        'host': os.getenv("DB_HOST", "localhost"),
        'port': _get_int("DB_PORT", 5432),
        'name': os.getenv("DB_NAME", "kb_mcp"),
        'user': os.getenv("DB_USER"),
        'password': os.getenv("DB_PASSWORD"),
        'schema': os.getenv("DB_SCHEMA", "public"),
        'sqlite_path': os.getenv("SQLITE_DB_PATH", "data/kb.db"),
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
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    return {
        'base_url': os.getenv("BASE_URL", "https://127.0.0.1"),
        'port': _get_int("PORT", 8443),
        'host': os.getenv("SERVER_HOST", "127.0.0.1"),
        'use_https': _get_bool("USE_HTTPS", True),
        'log_level': log_level,
        'mcp_log_level': os.getenv("MCP_LOG_LEVEL", log_level).upper(),
        'audit_log_file': os.getenv("AUDIT_LOG_FILE", ""),
        'max_upload_size': _get_int("MAX_UPLOAD_SIZE", 104857600),
        'use_firestore': _get_bool("SESSION_STORE_FIRESTORE", False),
        'site_name': os.getenv("SITE_NAME", "Knowledge Base"),
        'hide_graph': _get_bool("HIDE_GRAPH", False),
    }

# LLM
def get_default_llm_model() -> str:
    """Default LLM model used as fallback when specific model settings are not set.
    
    **Env Variable:** `DEFAULT_LLM_MODEL` (default: `gemini-2.5-flash-lite`).
    This is used as a fallback when specific model settings (e.g., SUMMARY_MODEL) are not set.
    """
    return os.getenv("DEFAULT_LLM_MODEL", "gemini-2.5-flash-lite")

def get_llm_config() -> dict:
    """All LLM settings.

    Returns:
        dict: LLM configuration with keys:

            * `openai_api_key` (str|None): API Key (Env: `OPENAI_API_KEY`).
            * `openai_base_url` (str|None): Custom API base URL (Env: `OPENAI_BASE_URL`).
            * `default_model` (str): Default model fallback (Env: `DEFAULT_LLM_MODEL`).
            * `summary_model` (str): Summarization model (Env: `SUMMARY_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `eval_gen_model` (str): Evaluation question generation model (Env: `EVAL_GEN_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `eval_judge_model` (str): Evaluation answer judging model (Env: `EVAL_JUDGE_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `image_description_model` (str): Model for image descriptions (Env: `PARSE_IMAGE_DESCRIPTION_MODEL`, defaults to a vision-capable model — see `DEFAULT_IMAGE_DESCRIPTION_MODEL`).
            * `graph_relation_extraction_model` (str): Graph relation extraction model (Env: `GRAPH_EXTRACTION_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `parser_comparison_model` (str): Parser comparison model (Env: `PARSER_COMP_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `privacy_filter_model` (str): Privacy classification model (Env: `PRIVACY_FILTER_MODEL`, defaults to DEFAULT_LLM_MODEL).
    """
    base_url_models = json.loads(os.getenv("OPENAI_BASE_URL_MODELS", "{}"))
    return {
        'openai_api_key': os.getenv("OPENAI_API_KEY"),
        'openai_base_url': os.getenv("OPENAI_BASE_URL"),
        'openai_base_url_models': base_url_models,
        'default_model': get_default_llm_model(),
        'summary_model': os.getenv("SUMMARY_MODEL", get_default_llm_model()),
        'eval_gen_model': os.getenv("EVAL_GEN_MODEL", get_default_llm_model()),
        'eval_judge_model': os.getenv("EVAL_JUDGE_MODEL", get_default_llm_model()),
        # Vision fallback, NOT the default LLM: text-only models silently
        # refuse images, so unset means a vision-capable default.
        'image_description_model': os.getenv("PARSE_IMAGE_DESCRIPTION_MODEL", DEFAULT_IMAGE_DESCRIPTION_MODEL),
        'graph_relation_extraction_model': os.getenv("GRAPH_EXTRACTION_MODEL", get_default_llm_model()),
        'parser_comparison_model': os.getenv("PARSER_COMP_MODEL", get_default_llm_model()),
        'privacy_filter_model': os.getenv("PRIVACY_FILTER_MODEL", get_default_llm_model()),
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
        'node_similarity_threshold': float(os.getenv("GRAPH_NODE_SIMILARITY_THRESHOLD", "0.85")),
        'embedding': get_embedding_config(),
        'graph_relation_extraction_model': os.getenv("GRAPH_EXTRACTION_MODEL", get_default_llm_model()),
        'domain': os.getenv("GRAPH_DOMAIN", None),  # e.g., "mu2e" for Mu2e-specific extraction
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
DEFAULT_IMAGE_DESCRIPTION_MODEL = "qwen/qwen3.6"


def get_parser_config() -> dict:
    """Parser settings.

    Returns:
        dict: Parser configuration with keys:

            * `parser` (str): Parser framework to use (Env: `KB_PARSER`, default: 'kb-mcp').
            * `image_additional_doc` (bool): Create separate docs for images (Env: `PARSE_IMAGE_ADDITIONAL_DOC`).
            * `image_llm_description` (bool): Use LLM for image descriptions (Env: `PARSE_IMAGE_LLM_DESCRIPTION`).
            * `image_description_model` (str): Model for descriptions (Env: `PARSE_IMAGE_DESCRIPTION_MODEL`, defaults to a vision-capable model — `qwen/qwen3.6`).
            * `image_description_num_workers` (int): Parallel worker count (Env: `PARSE_IMAGE_DESCRIPTION_NUMWORKERS`, default: 6).
            * `marker_output_base` (str): Base directory for pre-existing Marker output (Env: `MARKER_OUTPUT_BASE`, default: 'data/sources/sld-scanned/extracted_output').
            * `ocr` (bool): Run OCR during Docling PDF parsing (Env: `PARSE_OCR`, default: True). Required for scanned documents (e.g. SLD scans); born-digital-only sweeps can disable it for speed.
            * `table_llm_summary` (bool): Generate LLM summaries for table records (Env: `PARSE_TABLE_LLM_SUMMARY`, default: False).
            * `table_summary_model` (str): Model for table summaries (Env: `PARSE_TABLE_SUMMARY_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `table_summary_num_workers` (int): Parallel worker count (Env: `PARSE_TABLE_SUMMARY_NUMWORKERS`, default: 6).
    """
    return {
        'parser': os.getenv("KB_PARSER", "kb-mcp"),
        'image_additional_doc': _get_bool("PARSE_IMAGE_ADDITIONAL_DOC", True),
        'image_llm_description': _get_bool("PARSE_IMAGE_LLM_DESCRIPTION", True),
        'image_description_model': os.getenv("PARSE_IMAGE_DESCRIPTION_MODEL", DEFAULT_IMAGE_DESCRIPTION_MODEL),
        'image_description_num_workers': _get_int("PARSE_IMAGE_DESCRIPTION_NUMWORKERS", 6),
        'marker_output_base': os.getenv("MARKER_OUTPUT_BASE", "data/sources/sld-scanned/extracted_output"),
        # OCR on by default: scanned-document pipelines depend on it.
        # Born-digital-only sweeps can disable it per-deployment for speed.
        'ocr': _get_bool("PARSE_OCR", True),
        'table_llm_summary': _get_bool("PARSE_TABLE_LLM_SUMMARY", False),
        'table_summary_model': os.getenv("PARSE_TABLE_SUMMARY_MODEL", get_default_llm_model()),
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
            os.getenv("PARSE_FORMULA_ENRICHMENT_AUTO_THRESHOLD", "0.0005")
        ),
    }

def get_embedding_config() -> dict:
    """Embedding settings.

    Returns:
        dict: Embedding configuration with keys:

            * `provider` (str): Provider name (Env: `EMBEDDING_PROVIDER`, default: 'st').
            * `model` (str|None): Specific model name (Env: `EMBEDDING_MODEL`).
            * `chunk_strategy` (str): Chunking method (Env: `CHUNK_STRATEGY`, default: 'tokens').
            * `chunk_from_docling_json` (bool): Route PDF parents through the
              DoclingDocument-aware chunker that walks the persisted
              `documents.parser_output["body"]` and emits chunks with
              page_start/page_end/body_self_refs populated
              (Env: `CHUNK_FROM_DOCLING_JSON`, default: false). When true and
              the parent text doc's `parser_output` holds a DoclingDocument
              payload, the dispatch in `chunk_document()` uses
              `chunk_from_docling_json` instead of the Markdown
              token-windowing path.
    """
    return {
        'provider': os.getenv("EMBEDDING_PROVIDER", "st"),
        'model': os.getenv("EMBEDDING_MODEL"),
        'chunk_strategy': os.getenv("CHUNK_STRATEGY", "tokens"),
        'chunk_from_docling_json': _get_bool("CHUNK_FROM_DOCLING_JSON", False),
    }

def get_eval_config() -> dict:
    """Eval settings.

    Returns:
        dict: Evaluation configuration with keys:

            * `gen_model` (str): Question generation model (Env: `EVAL_GEN_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `judge_model` (str): Answer judging model (Env: `EVAL_JUDGE_MODEL`, defaults to DEFAULT_LLM_MODEL).
    """
    return {
        'gen_model': os.getenv("EVAL_GEN_MODEL", get_default_llm_model()),
        'judge_model': os.getenv("EVAL_JUDGE_MODEL", get_default_llm_model()),
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
        'model_name': os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
    }

def get_agent_config() -> dict:
    """Research agent configuration.

    Returns:
        dict: Agent configuration with keys:

            * `agent_model` (str): LLM model for agent reasoning (Env: `AGENT_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `max_depth` (int): Maximum recursion depth for agent delegation (Env: `AGENT_MAX_DEPTH`, default: 2).
    """
    return {
        'agent_model': os.getenv("AGENT_MODEL", get_default_llm_model()),
        'max_depth': _get_int("AGENT_MAX_DEPTH", 2),
        'max_tool_output_chars': _get_int("AGENT_MAX_TOOL_OUTPUT_CHARS", 30000),
        'max_aggregated_tool_output_chars': _get_int("AGENT_MAX_AGGREGATED_TOOL_OUTPUT_CHARS", 100000),
    }

# Paths
def get_data_dir() -> str:
    """Data directory. **Env Variable:** `DATA_DIR` (default: `data`)."""
    return os.getenv("DATA_DIR", "data")

def get_api_keys_file() -> str:
    """API keys file path. **Env Variable:** `API_KEYS_FILE`."""
    return os.getenv("API_KEYS_FILE", f"{get_data_dir()}/api_keys.json")

def get_all_config() -> dict:
    """Get all configuration (sanitized for logging).

    Returns:
        dict: A nested dictionary containing all configuration groups
              (database, server, llm, etc.) with secrets redacted.
    """
    return {
        'database': get_database_config(),
        'server': get_server_config(),
        'llm': {**get_llm_config(), 'openai_api_key': '***'},
        'github': {**get_github_oauth_config(), 'client_secret': '***'},
        'globus': {**get_globus_oauth_config(), 'client_secret': '***'},
        'auth': {k: v for k, v in get_auth_config().items() if k not in ['github', 'globus', 'oauth_provider']},
        'parser': get_parser_config(),
        'embedding': get_embedding_config(),
        'eval': get_eval_config(),
        'paths': {'data_dir': get_data_dir(), 'api_keys_file': get_api_keys_file()}
    }
