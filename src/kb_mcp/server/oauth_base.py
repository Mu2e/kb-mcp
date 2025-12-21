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
from .session_store import SessionStore
from ..config import get_api_keys_file, get_server_config

logger = logging.getLogger(__name__)


class BaseMCPOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken],
    ABC
):
    """Base class for MCP OAuth providers (GitHub, Globus, etc.)."""

    def __init__(self, client_id: str, client_secret: str):
        """Initialize base OAuth provider.
        
        Args:
            client_id: OAuth client ID
            client_secret: OAuth client secret
        """
        server_config = get_server_config()
        self.base_url = server_config['base_url']
        self.client_id = client_id
        self.client_secret = client_secret

        if not self.client_id or not self.client_secret:
            logger.warning(
                f"{self.provider_name.upper()}_CLIENT_ID and {self.provider_name.upper()}_CLIENT_SECRET not set - OAuth will not work"
            )

        # API key authentication - always enabled
        api_keys_file = get_api_keys_file()
        self.api_key_manager = ApiKeyManager(api_keys_file)
        logger.info(f"API key authentication enabled: {api_keys_file}")

        # Initialize session store for OAuth/MCP sessions
        self.session_store = SessionStore(collection_name="oauth_sessions")

        # Ephemeral state (not persisted - only valid during OAuth flow)
        self.pending_auth: dict[
            str, tuple[OAuthClientInformationFull, AuthorizationParams]
        ] = {}

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider name (e.g., 'github', 'globus')."""
        pass

    @property
    def display_name(self) -> str:
        """Return display name for the provider (defaults to capitalized provider_name)."""
        return self.provider_name.capitalize()

    @property
    def callback_path(self) -> str:
        """Return callback path (defaults to /oauth/callback)."""
        return "/oauth/callback"

    @property
    @abstractmethod
    def token_store_key(self) -> str:
        """Return key for storing tokens in session store (e.g., 'github_tokens', 'globus_tokens')."""
        pass

    @abstractmethod
    async def create_authorize_url(self, state: str, redirect_uri: str) -> str:
        """Create OAuth authorization URL.
        
        Args:
            state: OAuth state parameter
            redirect_uri: Callback redirect URI
            
        Returns:
            Full OAuth authorization URL
        """
        pass

    @abstractmethod
    async def exchange_code_for_token(self, code: str) -> str:
        """Exchange authorization code for access token.
        
        Args:
            code: Authorization code from OAuth callback
            
        Returns:
            Access token string
        """
        pass

    @abstractmethod
    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Get user information from access token.
        
        Args:
            access_token: OAuth access token
            
        Returns:
            Dictionary with user information
        """
        pass

    @abstractmethod
    async def verify_user_access(self, access_token: str) -> bool:
        """Verify user has required access.
        
        Args:
            access_token: OAuth access token
            
        Returns:
            True if user has required access
        """
        pass

    async def verify_user_admin_access(self, access_token: str) -> bool:
        """Verify user has admin-level access.
        
        Args:
            access_token: OAuth access token
            
        Returns:
            True if user has admin access, False otherwise.
            Default implementation returns False (no admin support).
        """
        # Default: no admin support
        return False

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
        logger.info(f"Authorization requested by client: {client.client_id}")
        logger.debug(
            f"Auth params: redirect_uri={params.redirect_uri}, state={params.state}"
        )

        # Generate state to track this authorization request
        state = secrets.token_urlsafe(32)
        self.pending_auth[state] = (client, params)

        # Build OAuth URL
        redirect_uri = f"{self.base_url}/oauth/callback"
        auth_url = await self.create_authorize_url(state, redirect_uri)

        logger.debug(f"Redirecting to {self.provider_name} OAuth: {auth_url[:100]}...")
        return auth_url

    async def handle_callback(self, code: str, state: str) -> str:
        """Handle OAuth callback for MCP OAuth flow."""
        if state not in self.pending_auth:
            raise AuthorizeError(
                "invalid_request",
                "Invalid state parameter - not MCP OAuth flow",
            )
        
        # MCP OAuth flow
        client, params = self.pending_auth.pop(state)

        # Exchange code for access token
        provider_token = await self.exchange_code_for_token(code)

        # Generate MCP authorization code
        auth_code = secrets.token_urlsafe(32)
        auth_code_obj = AuthorizationCode(
            code=auth_code,
            scopes=params.scopes or [],
            expires_at=time.time() + 600,  # 10 minutes
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )

        # Store authorization code and provider token
        await self.session_store.set(
            "auth_codes", auth_code, auth_code_obj.model_dump(mode="json")
        )
        await self.session_store.set(self.token_store_key, auth_code, provider_token)

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
        code_dict = await self.session_store.get("auth_codes", authorization_code)
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

        # Get provider token from SessionStore
        provider_token = await self.session_store.get(self.token_store_key, code_str)
        if not provider_token:
            raise TokenError("invalid_grant", "Authorization code not found")

        # Clean up used code and token
        await self.session_store.delete("auth_codes", code_str)
        await self.session_store.delete(self.token_store_key, code_str)

        # Generate MCP access token
        access_token_str = secrets.token_urlsafe(32)
        expires_in = 3600  # 1 hour

        access_token = AccessToken(
            token=access_token_str,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time() + expires_in),
            resource=authorization_code.resource,
        )

        # Store access token and provider token
        await self.session_store.set(
            "access_tokens", access_token_str, access_token.model_dump(mode="json")
        )
        await self.session_store.set(self.token_store_key, access_token_str, provider_token)

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
                await self.session_store.set("token_users", token, username)
                return AccessToken(
                    token=token,
                    client_id="api-key-client",
                    scopes=[],
                    expires_at=None,
                )
            else:
                logger.warning(f"Invalid API key: {token[:20]}...")
                return None

        # Otherwise, treat as OAuth token
        access_dict = await self.session_store.get("access_tokens", token)
        if not access_dict or not isinstance(access_dict, dict):
            logger.warning(f"Token not found: {token[:20]}...")
            return None

        access = AccessToken(**access_dict)

        if access.expires_at and time.time() > access.expires_at:
            logger.warning(f"Token expired: {token[:20]}...")
            return None

        # Verify with provider
        provider_token = await self.session_store.get(self.token_store_key, token)
        if not provider_token:
            logger.error(f"No {self.provider_name} token found for access token: {token[:20]}...")
            return None

        try:
            # Verify user
            user_data = await self.get_user_info(provider_token)
            username = self._extract_username(user_data)
            logger.info(f"{self.provider_name} user: {username}")

            # Store username for this token
            await self.session_store.set("token_users", token, username)

            # Check access
            has_access = await self.verify_user_access(provider_token)
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

    async def get_user_info_for_web(self, access_token: str) -> dict[str, Any]:
        """Get user info normalized for web use (includes 'username' key)."""
        user_data = await self.get_user_info(access_token)
        # Ensure 'username' key exists
        if "username" not in user_data:
            user_data["username"] = self._extract_username(user_data)
        return user_data

    async def get_active_sessions_count(self) -> int:
        """Get count of active sessions."""
        return await self.session_store.count_items("access_tokens")

    async def get_username_for_token(self, token: str) -> str | None:
        """Get username for a given access token."""
        return await self.session_store.get("token_users", token)

