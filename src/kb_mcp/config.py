"""
Configuration management for kb-mcp.
"""

import os
from typing import Optional
import json

from anyio import Path
from dotenv import load_dotenv, find_dotenv

# Load shared configuration from .env
load_dotenv()

# Load user-specific overrides from .env.local (takes precedence)
# This allows users to override settings (e.g., ALCF credentials) without
# modifying the shared .env file (which may be a symlink on NERSC)
env_path = find_dotenv()
if env_path:
    local_env_path = Path(env_path).with_name(".env.local")
    load_dotenv(dotenv_path=local_env_path, override=True)

# --- Helpers ---

def _get_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() == "true"

def _get_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    return int(value)

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
            * `host` (str): Server host (Env: `HOST`, default: '127.0.0.1').
            * `use_https` (bool): Whether HTTPS is enabled (Env: `USE_HTTPS`, default: True).
            * `log_level` (str): Logging level (Env: `LOG_LEVEL`, default: 'INFO').
            * `mcp_log_level` (str): App-specific log level (Env: `MCP_LOG_LEVEL`).
            * `audit_log_file` (str): Path to audit log (Env: `AUDIT_LOG_FILE`).
            * `max_upload_size` (int): Max upload bytes (Env: `MAX_UPLOAD_SIZE`, default: 100MB).
            * `use_firestore` (bool): Use Firestore for session storage (Env: `SESSION_STORE_FIRESTORE`, default: False).
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    return {
        'base_url': os.getenv("BASE_URL", "https://127.0.0.1"),
        'port': _get_int("PORT", 8443),
        'host': os.getenv("HOST", "127.0.0.1"),
        'use_https': _get_bool("USE_HTTPS", True),
        'log_level': log_level,
        'mcp_log_level': os.getenv("MCP_LOG_LEVEL", log_level).upper(),
        'audit_log_file': os.getenv("AUDIT_LOG_FILE", ""),
        'max_upload_size': _get_int("MAX_UPLOAD_SIZE", 104857600),
        'use_firestore': _get_bool("SESSION_STORE_FIRESTORE", False),
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
            * `image_llm_description` (bool): Use LLM for image descriptions (Env: `PARSE_IMAGE_DESCRIPTION_MODEL`).
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
        'image_description_model': os.getenv("PARSE_IMAGE_DESCRIPTION_MODEL", get_default_llm_model()),
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

def get_parser_config() -> dict:
    """Parser settings.

    Returns:
        dict: Parser configuration with keys:

            * `parser` (str): Parser framework to use (Env: `KB_PARSER`, default: 'kb-mcp').
            * `image_additional_doc` (bool): Create separate docs for images (Env: `PARSE_IMAGE_ADDITIONAL_DOC`).
            * `image_llm_description` (bool): Use LLM for image descriptions (Env: `PARSE_IMAGE_LLM_DESCRIPTION`).
            * `image_description_model` (str): Model for descriptions (Env: `PARSE_IMAGE_DESCRIPTION_MODEL`, defaults to DEFAULT_LLM_MODEL).
            * `image_description_num_workers` (int): Parallel worker count (Env: `PARSE_IMAGE_DESCRIPTION_NUMWORKERS`, default: 6).
            * `marker_output_base` (str): Base directory for pre-existing Marker output (Env: `MARKER_OUTPUT_BASE`, default: 'data/sources/sld-scanned/extracted_output').
    """
    return {
        'parser': os.getenv("KB_PARSER", "kb-mcp"),
        'image_additional_doc': _get_bool("PARSE_IMAGE_ADDITIONAL_DOC", True),
        'image_llm_description': _get_bool("PARSE_IMAGE_LLM_DESCRIPTION", True),
        'image_description_model': os.getenv("PARSE_IMAGE_DESCRIPTION_MODEL", get_default_llm_model()),
        'image_description_num_workers': _get_int("PARSE_IMAGE_DESCRIPTION_NUMWORKERS", 6),
        'marker_output_base': os.getenv("MARKER_OUTPUT_BASE", "data/sources/sld-scanned/extracted_output"),
    }

def get_embedding_config() -> dict:
    """Embedding settings.

    Returns:
        dict: Embedding configuration with keys:

            * `provider` (str): Provider name (Env: `EMBEDDING_PROVIDER`, default: 'st').
            * `model` (str|None): Specific model name (Env: `EMBEDDING_MODEL`).
            * `chunk_strategy` (str): Chunking method (Env: `CHUNK_STRATEGY`, default: 'tokens').
    """
    return {
        'provider': os.getenv("EMBEDDING_PROVIDER", "st"),
        'model': os.getenv("EMBEDDING_MODEL"),
        'chunk_strategy': os.getenv("CHUNK_STRATEGY", "tokens"),
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
