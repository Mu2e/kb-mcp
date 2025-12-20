"""
Configuration management for kb-mcp.
"""

import os
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Helpers ---

def _get_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() == "true"

def _get_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))

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
    }

# LLM
def get_default_llm_model() -> str:
    """Default LLM model. **Env Variable:** `DEFAULT_LLM_MODEL` (default: `gemini-2.5-flash-lite`)."""
    return os.getenv("DEFAULT_LLM_MODEL", "gemini-2.5-flash-lite")

def get_llm_config() -> dict:
    """All LLM settings.

    Returns:
        dict: LLM configuration with keys:

            * `openai_api_key` (str|None): API Key (Env: `OPENAI_API_KEY`).
            * `openai_base_url` (str|None): Custom API base URL (Env: `OPENAI_BASE_URL`).
            * `summary_model` (str): Summarization model (Env: `SUMMARY_MODEL`, default: `DEFAULT_LLM_MODEL`).
    """
    return {
        'openai_api_key': os.getenv("OPENAI_API_KEY"),
        'openai_base_url': os.getenv("OPENAI_BASE_URL"),
        'summary_model': os.getenv("SUMMARY_MODEL", get_default_llm_model()),
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

def get_web_session_config() -> dict:
    """Web session settings.

    Returns:
        dict: Web session configuration with keys:

            * `timeout` (int): Session timeout in seconds (Env: `WEB_SESSION_TIMEOUT`, default: 86400).
            * `reverify_interval` (int): Re-verify interval (Env: `WEB_REVERIFY_INTERVAL`, default: 3600).
            * `disable_auth` (bool): Security override (Env: `DISABLE_WEB_AUTH`, default: False).
            * `use_firestore` (bool): Storage backend (Env: `SESSION_STORE_FIRESTORE`, default: False).
    """
    return {
        'timeout': _get_int("WEB_SESSION_TIMEOUT", 86400),
        'reverify_interval': _get_int("WEB_REVERIFY_INTERVAL", 3600),
        'disable_auth': _get_bool("DISABLE_WEB_AUTH", False),
        'use_firestore': _get_bool("SESSION_STORE_FIRESTORE", False),
    }

# Processing
def get_parser_config() -> dict:
    """Parser settings.

    Returns:
        dict: Parser configuration with keys:

            * `image_additional_doc` (bool): Create separate docs for images (Env: `PARSE_IMAGE_ADDITIONAL_DOC`).
            * `image_llm_description` (bool): Use LLM for image descriptions (Env: `PARSE_IMAGE_LLM_DESCRIPTION`).
            * `image_description_model` (str): Model for descriptions (Env: `PARSE_IMAGE_DESCRIPTION_MODEL`).
            * `image_description_num_workers` (int): Parallel worker count (Env: `PARSE_IMAGE_DESCRIPTION_NUMWORKERS`, default: 6).
    """
    return {
        'image_additional_doc': _get_bool("PARSE_IMAGE_ADDITIONAL_DOC", False),
        'image_llm_description': _get_bool("PARSE_IMAGE_LLM_DESCRIPTION", False),
        'image_description_model': os.getenv("PARSE_IMAGE_DESCRIPTION_MODEL", get_default_llm_model()),
        'image_description_num_workers': _get_int("PARSE_IMAGE_DESCRIPTION_NUMWORKERS", 6),
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

            * `gen_model` (str): Question generation model (Env: `EVAL_GEN_MODEL`).
            * `judge_model` (str): Answer judging model (Env: `EVAL_JUDGE_MODEL`).
    """
    return {
        'gen_model': os.getenv("EVAL_GEN_MODEL", get_default_llm_model()),
        'judge_model': os.getenv("EVAL_JUDGE_MODEL", get_default_llm_model()),
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
        'web': get_web_session_config(),
        'parser': get_parser_config(),
        'embedding': get_embedding_config(),
        'eval': get_eval_config(),
        'paths': {'data_dir': get_data_dir(), 'api_keys_file': get_api_keys_file()}
    }