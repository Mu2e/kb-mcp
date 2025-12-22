"""Globus OAuth provider implementing MCP OAuth protocol."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.auth.provider import AuthorizeError

from .base import BaseOAuthProvider
from ...config import get_globus_oauth_config

logger = logging.getLogger(__name__)


class GlobusOAuthProvider(BaseOAuthProvider):
    """OAuth provider that uses Globus for authentication."""

    def __init__(self):
        globus_config = get_globus_oauth_config()
        super().__init__(globus_config['client_id'], globus_config['client_secret'])
        self.required_group = globus_config['required_group'] or None

        if self.required_group:
            logger.info(
                f"Access restricted to users in Globus group: {self.required_group}"
            )

    @property
    def provider_name(self) -> str:
        return "globus"

    async def create_authorize_url(self, state: str, redirect_uri: str) -> str:
        """Create Globus OAuth authorization URL."""
        from urllib.parse import urlencode
        
        # Include Groups API scope if group checking is needed
        scopes = ["openid", "profile", "email"]
        if self.required_group:
            scopes.append("urn:globus:auth:scope:groups.api.globus.org:all")
        
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": " ".join(scopes),
        }
        
        return f"https://auth.globus.org/v2/oauth2/authorize?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str) -> dict:
        """Exchange Globus authorization code for access token.

        Returns a dict with 'access_token' (main token) and optional 'groups_token'.
        """
        async with httpx.AsyncClient() as http_client:
            # Request the Groups API scope if needed
            scope = "openid profile email"
            if self.required_group:
                scope += " urn:globus:auth:scope:groups.api.globus.org:all"

            response = await http_client.post(
                "https://auth.globus.org/v2/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": f"{self.base_url}/oauth/callback",
                    "scope": scope,  # Explicitly request scope in token exchange
                },
                headers={"Accept": "application/json"},
            )
            data = response.json()

        if "error" in data:
            raise AuthorizeError(
                "access_denied", data.get("error_description", "Unknown error")
            )

        access_token = data["access_token"]
        result = {"access_token": access_token}

        # Extract Groups API token if present (for use in verify_user_access)
        if "other_tokens" in data and self.required_group:
            for other_token in data["other_tokens"]:
                if other_token.get("resource_server") == "groups.api.globus.org":
                    groups_token = other_token.get("access_token")
                    if groups_token:
                        result["groups_token"] = groups_token
                        logger.debug("Extracted Groups API token for group checking")

        return result

    async def get_user_info(self, access_token: str) -> dict[str, Any]:
        """Get Globus user information from access token."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://auth.globus.org/v2/oauth2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code != 200:
                raise AuthorizeError(
                    "access_denied", f"Failed to get user info: {response.status_code}"
                )
            return response.json()

    def _extract_username(self, user_data: dict[str, Any]) -> str:
        """Extract Globus username."""
        return user_data.get("preferred_username") or user_data.get("sub", "unknown")

    async def verify_user_access(self, provider_token_data: dict) -> bool:
        """
        Verify Globus group access.

        Uses ?include=my_memberships to check specific relationship
        without needing to list all group members.

        Args:
            provider_token_data: Dict with 'access_token' and optional 'groups_token'
        """
        if not self.required_group:
            return True

        # Extract groups_token from provider_token_data
        groups_token = provider_token_data.get("groups_token")
        if not groups_token:
            logger.warning(
                "No Groups API token found. Group checking requires Groups API scope."
            )
            return False

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://groups.api.globus.org/v2/groups/{self.required_group}",
                    params={"include": "my_memberships"},
                    headers={"Authorization": f"Bearer {groups_token}"},
                )

                if response.status_code == 200:
                    data = response.json()
                    # The API returns a list of memberships (usually just 1 if active)
                    my_memberships = data.get("my_memberships", [])

                    for membership in my_memberships:
                        if membership.get("status") == "active":
                            return True

                    logger.info(f"User is not active in group {self.required_group}")
                    return False

                elif response.status_code == 404:
                    logger.error(f"Configured Globus group ID {self.required_group} not found.")
                    return False
                else:
                    logger.warning(
                        f"Globus group check failed (Status {response.status_code}). "
                        f"Ensure token has 'urn:globus:auth:scope:groups.api.globus.org:all'"
                    )
                    return False

        except Exception as e:
            logger.error(f"Error checking Globus group access: {e}")
            return False

