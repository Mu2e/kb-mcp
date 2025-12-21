"""GitHub OAuth provider implementing MCP OAuth protocol (server package)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.auth.provider import AuthorizeError

from .oauth_base import BaseMCPOAuthProvider
from ..config import get_github_oauth_config

logger = logging.getLogger(__name__)


class GitHubOAuthProvider(BaseMCPOAuthProvider):
    """OAuth provider that uses GitHub for authentication."""

    def __init__(self):
        github_config = get_github_oauth_config()
        super().__init__(github_config['client_id'], github_config['client_secret'])
        self.required_repo = github_config['required_repo']  # Format: "owner/repo"

        if not self.required_repo:
            logger.warning(
                "GITHUB_REQUIRED_REPO not set - all GitHub users will be allowed"
            )
        else:
            logger.info(
                f"Access restricted to users with access to: {self.required_repo}"
            )

    @property
    def provider_name(self) -> str:
        return "github"

    @property
    def display_name(self) -> str:
        return "GitHub"

    @property
    def token_store_key(self) -> str:
        return "github_tokens"

    async def create_authorize_url(self, state: str, redirect_uri: str) -> str:
        """Create GitHub OAuth authorization URL."""
        return (
            "https://github.com/login/oauth/authorize"
            f"?client_id={self.client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
            f"&scope=read:user repo"
        )

    async def exchange_code_for_token(self, code: str) -> str:
        """Exchange GitHub authorization code for access token."""
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                },
            )
            data = response.json()

        if "error" in data:
            raise AuthorizeError(
                "access_denied", data.get("error_description", "Unknown error")
            )

        return data["access_token"]

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Get GitHub user information from access token."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code != 200:
                raise AuthorizeError(
                    "access_denied", f"Failed to get user info: {response.status_code}"
                )
            return response.json()

    def _extract_username(self, user_data: dict[str, Any]) -> str:
        """Extract GitHub username."""
        return user_data.get("login", "unknown")

    async def verify_user_access(self, access_token: str) -> bool:
        """Verify GitHub repository access."""
        if not self.required_repo:
            return True  # No repo restriction
        return await self.verify_repo_access(access_token)

    async def verify_user_admin_access(self, access_token: str) -> bool:
        """Verify GitHub repository admin access."""
        if not self.required_repo:
            return False  # No repo restriction means no admin check possible
        return await self.verify_repo_access(access_token, require_admin=True)

    async def verify_repo_access(
        self, github_token: str, repo: str | None = None, require_admin: bool = False
    ) -> bool:
        """Verify user has access to a GitHub repository."""
        repo = repo or self.required_repo
        if not repo:
            return True

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

