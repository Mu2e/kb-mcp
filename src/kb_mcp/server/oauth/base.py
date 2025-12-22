"""Base OAuth provider implementing MCP OAuth protocol."""

from __future__ import annotations

import logging
import secrets
import time
from abc import ABC, abstractmethod
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .api_keys import ApiKeyManager
from ..session_store import SessionStore
from ...config import get_api_keys_file, get_server_config, get_auth_config

logger = logging.getLogger(__name__)


class BaseOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken],
    ABC
):
    """Base class for OAuth providers (GitHub, Globus, etc.).

    Supports both OAuth authentication and API key authentication.
    If OAuth credentials are not provided, operates in API-key-only mode.
    """

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        """Initialize base OAuth provider.
        
        Args:
            client_id: OAuth client ID (optional - if None, operates in API-key-only mode)
            client_secret: OAuth client secret (optional - if None, operates in API-key-only mode)
        """
        server_config = get_server_config()
        self.base_url = server_config['base_url']
        self.client_id = client_id
        self.client_secret = client_secret

        # Load authentication timeout settings
        auth_config = get_auth_config()
        self.authorization_code_timeout = auth_config['authorization_code_timeout']
        self.access_token_timeout = auth_config['access_token_timeout']
        self.oauth_state_timeout = auth_config['oauth_state_timeout']

        if not self._is_oauth_enabled:
            logger.info("OAuth credentials not provided - operating in API-key-only mode")
            logger.info("To enable OAuth, set {self.provider_name.upper()}_CLIENT_ID and {self.provider_name.upper()}_CLIENT_SECRET")

        # API key authentication - always enabled
        api_keys_file = get_api_keys_file()
        self.api_key_manager = ApiKeyManager(api_keys_file)
        logger.info(f"API key authentication enabled: {api_keys_file}")

        # Initialize session store for OAuth/MCP sessions
        self.session_store = SessionStore(collection_name="oauth_sessions")

    @property
    def _is_oauth_enabled(self) -> bool:
        """Check if OAuth is enabled (credentials provided)."""
        return bool(self.client_id and self.client_secret)

    @property
    def provider_name(self) -> str:
        """Return provider name (e.g., 'github', 'globus', 'api-key')."""
        # Default for API-key-only mode
        if not self._is_oauth_enabled:
            return "api-key"
        # Subclasses must override this when OAuth is enabled
        raise NotImplementedError("Subclasses must implement provider_name when OAuth is enabled")

    @property
    def callback_path(self) -> str:
        """Return callback path (defaults to /oauth/callback)."""
        return "/oauth/callback"

    async def create_authorize_url(self, state: str, redirect_uri: str) -> str:
        """Create OAuth authorization URL.
        
        Args:
            state: OAuth state parameter
            redirect_uri: Callback redirect URI
            
        Returns:
            Full OAuth authorization URL
        """
        if not self._is_oauth_enabled:
            raise AuthorizeError(
                "oauth_not_available",
                "OAuth not available. API key authentication only. Please configure GitHub or Globus OAuth for web access."
            )
        # Subclasses must override this when OAuth is enabled
        raise NotImplementedError("Subclasses must implement create_authorize_url when OAuth is enabled")

    async def exchange_code_for_token(self, code: str) -> str:
        """Exchange authorization code for access token.
        
        Args:
            code: Authorization code from OAuth callback
            
        Returns:
            Access token string
        """
        if not self._is_oauth_enabled:
            raise AuthorizeError(
                "oauth_not_available",
                "OAuth not available. API key authentication only."
            )
        # Subclasses must override this when OAuth is enabled
        raise NotImplementedError("Subclasses must implement exchange_code_for_token when OAuth is enabled")

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Get user information from access token.
        
        Args:
            access_token: OAuth access token
            
        Returns:
            Dictionary with user information
        """
        if not self._is_oauth_enabled:
            raise AuthorizeError(
                "oauth_not_available",
                "OAuth not available. API key authentication only."
            )
        # Subclasses must override this when OAuth is enabled
        raise NotImplementedError("Subclasses must implement get_user_info when OAuth is enabled")

    async def verify_user_access(self, provider_token_data: dict) -> bool:
        """Verify user has required access.

        Args:
            provider_token_data: Dict with 'access_token' and optional provider-specific extras

        Returns:
            True if user has required access
        """
        if not self._is_oauth_enabled:
            # API-key-only mode: access verification not applicable
            return False
        # Subclasses must override this when OAuth is enabled
        raise NotImplementedError("Subclasses must implement verify_user_access when OAuth is enabled")

    async def verify_user_admin_access(self, access_token: str) -> bool:
        """Verify user has admin-level access.
        
        Args:
            access_token: OAuth access token
            
        Returns:
            True if user has admin access, False otherwise.
            Default implementation returns True (no additional admin checks for API-key-only mode).
        """
        # API-key-only no additional admin checks: return True
        return True

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Get registered MCP client."""
        client_dict = await self.session_store.get("clients", client_id)
        if client_dict and isinstance(client_dict, dict):
            return OAuthClientInformationFull(**client_dict)
        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register MCP client."""
        await self.session_store.set(
            "clients", client_info.client_id, client_info.model_dump(mode="json")
        )
        logger.info(
            f"Registered MCP client: {client_info.client_id}, "
            f"redirect_uris={client_info.redirect_uris}"
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Start OAuth flow by redirecting to provider."""
        if not self._is_oauth_enabled:
            raise AuthorizeError(
                "oauth_not_available",
                "OAuth not available. API key authentication only. Please configure GitHub or Globus OAuth for OAuth flows."
            )
        
        logger.info(f"Authorization requested by client: {client.client_id}")
        logger.debug(
            f"Auth params: redirect_uri={params.redirect_uri}, state={params.state}"
        )

        # Generate state to track this authorization request
        state = secrets.token_urlsafe(32)

        # Store pending authorization in session store with expiration
        await self.session_store.set(
            "pending_auth",
            state,
            {
                "client": client.model_dump(mode="json"),
                "params": params.model_dump(mode="json"),
                "expires_at": time.time() + self.oauth_state_timeout,
            }
        )

        # Build OAuth URL
        redirect_uri = f"{self.base_url}/oauth/callback"
        auth_url = await self.create_authorize_url(state, redirect_uri)

        logger.debug(f"Redirecting to {self.provider_name} OAuth: {auth_url[:100]}...")
        return auth_url

    async def handle_callback(self, code: str, state: str) -> str:
        """Handle OAuth callback for MCP OAuth flow."""
        if not self._is_oauth_enabled:
            raise AuthorizeError(
                "oauth_not_available",
                "OAuth not available. API key authentication only. Please configure GitHub or Globus OAuth for OAuth flows."
            )

        # Load pending authorization from session store
        pending_data = await self.session_store.get("pending_auth", state)
        if not pending_data or not isinstance(pending_data, dict):
            raise AuthorizeError(
                "invalid_request",
                "Invalid state parameter - not MCP OAuth flow",
            )

        # Check expiration
        expires_at = pending_data.get("expires_at")
        if expires_at and time.time() > expires_at:
            await self.session_store.delete("pending_auth", state)
            raise AuthorizeError(
                "invalid_request",
                "State expired",
            )

        # Reconstruct client and params from stored data
        client = OAuthClientInformationFull(**pending_data["client"])
        params = AuthorizationParams(**pending_data["params"])

        # Clean up used state
        await self.session_store.delete("pending_auth", state)

        # Exchange code for access token (returns dict with 'access_token' and optional extras)
        provider_token_data = await self.exchange_code_for_token(code)

        # Generate MCP authorization code
        auth_code = secrets.token_urlsafe(32)
        auth_code_obj = AuthorizationCode(
            code=auth_code,
            scopes=params.scopes or [],
            expires_at=time.time() + self.authorization_code_timeout,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )

        # Store authorization code with provider token data embedded
        await self.session_store.set(
            "auth_codes",
            auth_code,
            {
                "auth_code": auth_code_obj.model_dump(mode="json"),
                "provider_token_data": provider_token_data,  # Store entire dict (includes access_token and extras)
                "provider": self.provider_name,
                "expires_at": auth_code_obj.expires_at,  # Top-level for cleanup
            }
        )

        # Redirect back to MCP client
        redirect_url = str(params.redirect_uri)
        separator = "&" if "?" in redirect_url else "?"
        redirect_url += f"{separator}code={auth_code}"
        if params.state:
            redirect_url += f"&state={params.state}"

        return redirect_url

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        """Load MCP authorization code."""
        stored_data = await self.session_store.get("auth_codes", authorization_code)
        if stored_data and isinstance(stored_data, dict):
            code_dict = stored_data.get("auth_code")
            if code_dict and isinstance(code_dict, dict):
                code = AuthorizationCode(**code_dict)
                if code.client_id == client.client_id:
                    if time.time() < code.expires_at:
                        return code
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """Exchange MCP authorization code for MCP access token."""
        code_str = authorization_code.code

        # Get stored auth code data (includes provider token data)
        stored_data = await self.session_store.get("auth_codes", code_str)
        if not stored_data or not isinstance(stored_data, dict):
            raise TokenError("invalid_grant", "Authorization code not found")

        provider_token_data = stored_data.get("provider_token_data")
        if not provider_token_data:
            raise TokenError("invalid_grant", "Provider token not found")

        # Clean up used code
        await self.session_store.delete("auth_codes", code_str)

        # Generate MCP access token
        access_token_str = secrets.token_urlsafe(32)
        expires_in = self.access_token_timeout

        access_token = AccessToken(
            token=access_token_str,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time() + expires_in),
            resource=authorization_code.resource,
        )

        # Store access token with all associated data in one place
        await self.session_store.set(
            "access_tokens",
            access_token_str,
            {
                "access_token": access_token.model_dump(mode="json"),
                "provider": self.provider_name,
                "provider_token_data": provider_token_data,  # Store entire dict (includes access_token and extras like groups_token)
                "username": None,  # Will be populated on first use
                "expires_at": access_token.expires_at,  # Top-level for cleanup
            }
        )

        logger.info(f"Issued access token for client {client.client_id}")

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(authorization_code.scopes)
            if authorization_code.scopes
            else None,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token_str: str
    ) -> OAuthToken:
        """Refresh tokens not implemented."""
        raise TokenError("unsupported_grant_type", "Refresh tokens not supported")

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Load and verify access token (API key or OAuth token)."""
        logger.debug(f"Loading token: {token[:20]}...")

        # Check if this is an API key (format: sk_...)
        if token.startswith("sk_"):
            username = self.api_key_manager.verify_key(token)
            if username:
                logger.info(f"Valid API key for user: {username}")
                return AccessToken(
                    token=token,
                    client_id="api-key-client",
                    scopes=[],
                    expires_at=None,
                )
            else:
                logger.warning(f"Invalid API key: {token[:20]}...")
                return None

        # Otherwise, treat as OAuth token - load consolidated data
        stored_data = await self.session_store.get("access_tokens", token)
        if not stored_data or not isinstance(stored_data, dict):
            logger.warning(f"Token not found: {token[:20]}...")
            return None

        # Extract AccessToken from consolidated structure
        access_dict = stored_data.get("access_token")
        if not access_dict or not isinstance(access_dict, dict):
            logger.error(f"Invalid token structure: {token[:20]}...")
            return None

        access = AccessToken(**access_dict)

        if access.expires_at and time.time() > access.expires_at:
            logger.warning(f"Token expired: {token[:20]}...")
            return None

        # Get provider token data from consolidated structure
        provider_token_data = stored_data.get("provider_token_data")
        if not provider_token_data:
            logger.error(f"No provider token found for access token: {token[:20]}...")
            return None

        # Extract main access_token from provider_token_data
        provider_token = provider_token_data.get("access_token")
        if not provider_token:
            logger.error(f"No access_token in provider_token_data: {token[:20]}...")
            return None

        # Check if username is cached
        cached_username = stored_data.get("username")

        try:
            # Verify user (only if not cached or for periodic re-verification)
            if not cached_username:
                user_data = await self.get_user_info(provider_token)
                username = self._extract_username(user_data)
                logger.info(f"{self.provider_name} user: {username}")

                # Update cached username in stored data
                stored_data["username"] = username
                await self.session_store.set("access_tokens", token, stored_data)
            else:
                username = cached_username
                logger.debug(f"Using cached username: {username}")

            # Check access (pass entire provider_token_data dict)
            has_access = await self.verify_user_access(provider_token_data)
            if not has_access:
                logger.warning(f"User {username} does NOT have required access")
                return None
        except AuthorizeError as e:
            logger.warning(f"{self.provider_name} verification failed: {e.description}")
            return None

        logger.debug(f"Token verified successfully: {token[:20]}...")
        return access

    def _extract_username(self, user_data: dict[str, Any]) -> str:
        """Extract username from user data (provider-specific)."""
        # Default implementation - subclasses can override
        return user_data.get("username") or user_data.get("login") or user_data.get("preferred_username") or user_data.get("sub", "unknown")

    async def get_active_sessions_count(self) -> int:
        """Get count of active sessions."""
        return await self.session_store.count_items("access_tokens")

    async def get_username_for_token(self, token: str) -> str | None:
        """Get username for a given access token."""
        # For API keys, check the API key manager
        if token.startswith("sk_"):
            return self.api_key_manager.verify_key(token)

        # For OAuth tokens, get from consolidated access_tokens structure
        stored_data = await self.session_store.get("access_tokens", token)
        if stored_data and isinstance(stored_data, dict):
            return stored_data.get("username")
        return None

