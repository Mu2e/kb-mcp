"""GitHub OAuth provider implementing MCP OAuth protocol."""

import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
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

logger = logging.getLogger(__name__)


class GitHubOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth provider that uses GitHub for authentication."""

    def __init__(self):
        self.base_url = os.getenv("BASE_URL", "https://127.0.0.1")
        self.github_client_id = os.getenv("GITHUB_CLIENT_ID", "")
        self.github_client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")
        self.required_repo = os.getenv("GITHUB_REQUIRED_REPO", "")  # Format: "owner/repo"

        if not self.github_client_id or not self.github_client_secret:
            logger.warning("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET not set - OAuth will not work")

        if not self.required_repo:
            logger.warning("GITHUB_REQUIRED_REPO not set - all GitHub users will be allowed")
        else:
            logger.info(f"Access restricted to users with access to: {self.required_repo}")

        # API key authentication - always enabled (data/ folder created automatically)
        api_keys_file = os.getenv("API_KEYS_FILE", "data/api_keys.json")
        self.api_key_manager = ApiKeyManager(api_keys_file)
        logger.info(f"API key authentication enabled: {api_keys_file}")

        # Storage
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending_auth: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.github_tokens: dict[str, str] = {}  # session_id -> github_token
        self.token_users: dict[str, str] = {}  # access_token -> github_username
        self.admin_callback_handler = None  # Set by server.py after admin routes are setup

    def set_admin_callback_handler(self, handler):
        """Set the admin callback handler for multiplexing OAuth callbacks."""
        self.admin_callback_handler = handler

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Get registered MCP client."""
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Register MCP client."""
        self.clients[client_info.client_id] = client_info
        logger.info(f"Registered MCP client: {client_info.client_id}, redirect_uris={client_info.redirect_uris}")

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """
        Start OAuth flow by redirecting to GitHub.
        MCP client will open this URL in a browser.
        """
        logger.info(f"Authorization requested by client: {client.client_id}")
        logger.debug(f"Auth params: redirect_uri={params.redirect_uri}, state={params.state}")

        # Generate state to track this authorization request
        state = secrets.token_urlsafe(32)
        self.pending_auth[state] = (client, params)

        # Build GitHub OAuth URL
        # We'll redirect back to our server's callback, which will then redirect to the MCP client
        github_auth_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={self.github_client_id}"
            f"&redirect_uri={self.base_url}/oauth/github/callback"
            f"&state={state}"
            f"&scope=read:user repo" # we could add read:org if we want to check org membership
        )

        logger.debug(f"Redirecting to GitHub OAuth: {github_auth_url[:100]}...")

        return github_auth_url

    async def handle_github_callback(self, code: str, state: str) -> str:
        """
        Handle GitHub callback (multiplexed for both MCP OAuth and admin login):
        - If state starts with "admin_", route to admin callback handler
        - Otherwise, handle MCP OAuth flow:
          1. Exchange GitHub code for GitHub access token
          2. Generate our MCP authorization code
          3. Redirect back to MCP client
        """
        # Check if this is an admin callback (state prefixed with "admin_")
        if state.startswith("admin_"):
            if self.admin_callback_handler:
                return await self.admin_callback_handler(code, state)
            else:
                raise AuthorizeError("invalid_request", "Admin callback handler not configured")

        # MCP OAuth flow
        if state not in self.pending_auth:
            raise AuthorizeError("invalid_request", "Invalid state parameter")

        client, params = self.pending_auth.pop(state)

        # Exchange code for GitHub access token
        github_token = await self.exchange_github_code(code)

        # Generate MCP authorization code
        auth_code = secrets.token_urlsafe(32)
        self.auth_codes[auth_code] = AuthorizationCode(
            code=auth_code,
            scopes=params.scopes or [],
            expires_at=time.time() + 600,  # 10 minutes
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )

        # Store GitHub token linked to this auth code
        self.github_tokens[auth_code] = github_token

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
        code = self.auth_codes.get(authorization_code)
        if code and code.client_id == client.client_id:
            if time.time() < code.expires_at:
                return code
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """Exchange MCP authorization code for MCP access token."""
        code_str = authorization_code.code

        # Get GitHub token
        github_token = self.github_tokens.get(code_str)
        if not github_token:
            raise TokenError("invalid_grant", "Authorization code not found")

        # Clean up used code
        if code_str in self.auth_codes:
            del self.auth_codes[code_str]
            del self.github_tokens[code_str]

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

        self.access_tokens[access_token_str] = access_token
        self.github_tokens[access_token_str] = github_token

        logger.info(f"Issued access token for client {client.client_id}")
        logger.debug(f"Token: {access_token_str[:20]}..., total tokens: {len(self.access_tokens)}")

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token_str: str
    ) -> OAuthToken:
        """Refresh tokens not implemented."""
        raise TokenError("unsupported_grant_type", "Refresh tokens not supported")

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Load and verify access token (API key or OAuth token)."""
        logger.debug(f"Loading token: {token[:20]}...")

        # Check if this is an API key (format: sk_live_...)
        if token.startswith("sk_live_"):
            username = self.api_key_manager.verify_key(token)
            if username:
                logger.info(f"Valid API key for user: {username}")
                # Store username for audit logging
                self.token_users[token] = username
                # Return a synthetic AccessToken (API keys don't expire)
                return AccessToken(
                    token=token,
                    client_id="api-key-client",  # Synthetic client ID for API keys
                    scopes=[],  # API keys have full access
                    expires_at=None,  # No expiration for API keys
                )
            else:
                logger.warning(f"Invalid API key: {token[:20]}...")
                return None

        # Otherwise, treat as OAuth token
        access = self.access_tokens.get(token)
        if not access:
            logger.warning(f"Token not found: {token[:20]}...")
            return None

        if access.expires_at and time.time() > access.expires_at:
            logger.warning(f"Token expired: {token[:20]}...")
            return None

        # Verify with GitHub
        github_token = self.github_tokens.get(token)
        if not github_token:
            logger.error(f"No GitHub token found for access token: {token[:20]}...")
            return None

        try:
            # Verify user
            user_data = await self.get_github_user(github_token)
            username = user_data.get('login')
            logger.info(f"GitHub user: {username} (id: {user_data.get('id')})")

            # Store username for this token
            self.token_users[token] = username

            # Check repository access if required
            if self.required_repo:
                logger.debug(f"Checking access to repository: {self.required_repo}")
                has_access = await self.verify_repo_access(github_token)
                if has_access:
                    logger.info(f"User {username} has access to {self.required_repo}")
                else:
                    logger.warning(f"User {username} does NOT have access to {self.required_repo}")
                    return None
        except AuthorizeError as e:
            logger.warning(f"GitHub verification failed: {e.description}")
            return None

        logger.debug(f"Token verified successfully: {token[:20]}...")
        return access

    def get_active_sessions_count(self) -> int:
        """Get count of active sessions."""
        return len(self.access_tokens)

    def get_username_for_token(self, token: str) -> str | None:
        """Get GitHub username for a given access token."""
        return self.token_users.get(token)

    # GitHub API helper methods (shared between OAuth and admin)
    async def exchange_github_code(self, code: str, client_secret: bool = True) -> str:
        """
        Exchange GitHub authorization code for GitHub access token.

        Args:
            code: GitHub authorization code
            client_secret: Whether to include client_secret (True for confidential clients,
                          False for PKCE flows - though we always use client_secret in this app)

        Returns:
            GitHub access token

        Raises:
            AuthorizeError: If exchange fails
        """
        async with httpx.AsyncClient() as http_client:
            data = {
                "client_id": self.github_client_id,
                "code": code,
            }
            if client_secret:
                data["client_secret"] = self.github_client_secret

            response = await http_client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data=data,
            )
            github_data = response.json()

        if "error" in github_data:
            raise AuthorizeError("access_denied", github_data.get("error_description", "Unknown error"))

        return github_data["access_token"]

    async def get_github_user(self, github_token: str) -> dict[str, Any]:
        """
        Get GitHub user information from access token.

        Args:
            github_token: GitHub access token

        Returns:
            GitHub user data dict with 'login', 'id', etc.

        Raises:
            AuthorizeError: If user info fetch fails
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {github_token}"},
            )
            if response.status_code != 200:
                raise AuthorizeError("access_denied", f"Failed to get user info: {response.status_code}")

            return response.json()

    async def verify_repo_access(self, github_token: str, repo: str | None = None, require_admin: bool = False) -> bool:
        """
        Verify user has access to a GitHub repository.

        Args:
            github_token: GitHub access token
            repo: Repository in 'owner/repo' format (defaults to self.required_repo)
            require_admin: If True, require admin permissions on the repo

        Returns:
            True if user has access (and admin permission if required), False otherwise
        """
        repo = repo or self.required_repo
        if not repo:
            return True  # No repo restriction

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{repo}",
                headers={"Authorization": f"Bearer {github_token}"},
            )
            if response.status_code != 200:
                return False

            # If admin permission is required, check permissions
            if require_admin:
                repo_data = response.json()
                permissions = repo_data.get("permissions", {})
                return permissions.get("admin", False)

            return True
