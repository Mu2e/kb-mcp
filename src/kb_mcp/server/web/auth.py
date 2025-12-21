"""Shared authentication for web interfaces (admin and web) - server package."""

from __future__ import annotations

import logging
import os
import secrets
import time
from starlette.responses import HTMLResponse, RedirectResponse

from ..session_store import SessionStore
from ..oauth_base import BaseMCPOAuthProvider

logger = logging.getLogger(__name__)


class WebSessionManager:
    """Manages browser sessions for web interfaces."""

    def __init__(self, oauth_provider: BaseMCPOAuthProvider | None):
        """Initialize with a single OAuth provider (or None if auth disabled)."""
        from ...config import get_web_session_config

        self.oauth_provider = oauth_provider
        web_config = get_web_session_config()

        # Initialize session store for persistence (local at data/web_sessions.json or firestore)
        # Lazy loading: data loads on first access
        self.session_store = SessionStore(collection_name="web_sessions")

        # Session timeout in seconds (default: 24 hours)
        self.session_timeout = web_config['timeout']
        # Re-verification interval in seconds (default: 1 hour)
        self.reverify_interval = web_config['reverify_interval']

        # Flag to disable authentication (for local development only)
        # Default: authentication is REQUIRED (secure by default)
        disable_auth = web_config['disable_auth']
        self.require_auth = not disable_auth
        if disable_auth:
            logger.warning(
                "WEB AUTHENTICATION DISABLED - For development only! "
                "Remove DISABLE_WEB_AUTH or set DISABLE_WEB_AUTH=false for production."
            )
        logger.info(
            f"Web session timeout: {self.session_timeout} seconds "
            f"({self.session_timeout / 3600:.1f} hours)"
        )
        logger.info(
            f"Web re-verification interval: {self.reverify_interval} seconds "
            f"({self.reverify_interval / 3600:.1f} hours)"
        )

    async def get_session_data(self, request, force_reverify=False):
        """Get full session data from browser session cookie with re-verification."""
        session_id = request.cookies.get("web_session")

        # If no session cookie, redirect to login (same flow for both dev and prod)
        if not session_id:
            return None

        # If auth is disabled, use dev session without GitHub verification
        if not self.require_auth:
            session_data = await self.session_store.get("sessions", session_id)
            if not session_data:
                # Session not found, return None to trigger redirect to login
                return None

            # Check if session has expired
            current_time = time.time()
            expires_at = session_data.get("expires_at")
            if expires_at and current_time > expires_at:
                # Session expired, clean it up
                await self.session_store.delete("sessions", session_id)
                return None

            # Update expiration (sliding expiration)
            session_data["expires_at"] = current_time + self.session_timeout
            await self.session_store.set("sessions", session_id, session_data)
            return session_data

        # Normal authentication flow (require_auth=True)

        session_data = await self.session_store.get("sessions", session_id)
        if not session_data:
            return None

        current_time = time.time()

        # Check if session has expired
        expires_at = session_data.get("expires_at")
        if expires_at and current_time > expires_at:
            # Session expired, clean it up
            logger.info(f"Session expired for user: {session_data.get('username')}")
            await self.session_store.delete("sessions", session_id)
            return None

        # Sliding expiration - extend session on each request
        session_data["expires_at"] = current_time + self.session_timeout
        # Persist updated expiration
        await self.session_store.set("sessions", session_id, session_data)

        # Periodic re-verification with GitHub (or forced for admin pages)
        last_verified = session_data.get("last_verified", 0)
        should_reverify = force_reverify or (
            current_time - last_verified > self.reverify_interval
        )

        if should_reverify:
            access_token = session_data.get("access_token")
            provider_name = session_data.get("provider", "github")  # Default to github for backward compatibility
            
            if not access_token:
                # No access token stored (old session), invalidate
                logger.warning(
                    f"No access token in session for user: {session_data.get('username')}"
                )
                await self.session_store.delete("sessions", session_id)
                return None

            # Get the provider used for this session
            if not self.oauth_provider or self.oauth_provider.provider_name != provider_name:
                logger.warning(
                    f"Provider {provider_name} mismatch, invalidating session"
                )
                await self.session_store.delete("sessions", session_id)
                return None

            try:
                # Re-verify user with provider
                user_data = await self.oauth_provider.get_user_info_for_web(access_token)
                username = user_data.get("username")

                # Re-verify access and admin permissions
                has_access = await self.oauth_provider.verify_user_access(access_token)
                if not has_access:
                    logger.warning(
                        f"User {username} no longer has required access"
                    )
                    await self.session_store.delete("sessions", session_id)
                    return None

                # Check admin access
                has_admin = await self.oauth_provider.verify_user_admin_access(access_token)

                # Update session with re-verified data
                session_data["username"] = username
                session_data["has_admin"] = has_admin
                session_data["last_verified"] = current_time
                # Persist updated session data
                await self.session_store.set("sessions", session_id, session_data)
                if force_reverify:
                    logger.debug(
                        f"Re-verified admin session for {username} (has_admin={has_admin})"
                    )
                else:
                    logger.info(
                        f"Re-verified session for {username} (has_admin={has_admin})"
                    )

            except Exception as e:
                # Re-verification failed, invalidate session
                logger.error(
                    f"Session re-verification failed for {session_data.get('username')}: {e}"
                )
                await self.session_store.delete("sessions", session_id)
                return None

        return session_data

    async def get_session_username(self, request, force_reverify=False):
        """Get username from browser session cookie."""
        session_data = await self.get_session_data(
            request, force_reverify=force_reverify
        )
        if not session_data:
            return None
        return session_data.get("username")

    async def has_admin_access(self, request, force_reverify=False):
        """Check if session has admin access."""
        session_data = await self.get_session_data(
            request, force_reverify=force_reverify
        )
        if not session_data:
            return False
        return session_data.get("has_admin", False)

    def get_auth_warning_html(self) -> str:
        """Get HTML warning banner if authentication is disabled."""
        if not self.require_auth:
            return """
            <div style="background: #fff3cd; border: 1px solid #ffc107; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                <strong>Development Mode:</strong> Web authentication is disabled (DISABLE_WEB_AUTH=true).
                Remove DISABLE_WEB_AUTH or set DISABLE_WEB_AUTH=false for production deployments.
            </div>
            """
        return ""

    async def create_oauth_login_url(
        self, provider: BaseMCPOAuthProvider, redirect_after_login: str = "/"
    ) -> tuple[str, str]:
        """Create OAuth login URL for a specific provider."""
        state = secrets.token_urlsafe(32)
        # Store redirect path and provider in state mapping with expiration (OAuth flows timeout after 10 minutes)
        current_time = time.time()
        expires_at = current_time + 600  # 10 minutes
        await self.session_store.set(
            "sessions",
            f"redirect_{state}",
            {"value": redirect_after_login, "expires_at": expires_at},
        )
        await self.session_store.set(
            "sessions",
            f"provider_{state}",
            {"value": provider.provider_name, "expires_at": expires_at},
        )

        redirect_uri = f"{provider.base_url}{provider.callback_path}"
        oauth_url = await provider.create_authorize_url(state, redirect_uri)
        return oauth_url, state

    async def handle_oauth_callback(self, provider: BaseMCPOAuthProvider, code: str, state: str):
        """Handle OAuth callback and create session."""
        # Verify state was valid
        state_data = await self.session_store.get("sessions", f"state_{state}")
        if not isinstance(state_data, dict) or "value" not in state_data:
            return HTMLResponse("Invalid state", status_code=400)

        # Check expiration
        expires_at = state_data.get("expires_at")
        if expires_at and time.time() > expires_at:
            await self.session_store.delete("sessions", f"state_{state}")
            return HTMLResponse("State expired", status_code=400)

        state_valid = state_data.get("value", False)
        if not state_valid:
            return HTMLResponse("Invalid state", status_code=400)

        await self.session_store.delete("sessions", f"state_{state}")

        # Get redirect path from state
        redirect_data = await self.session_store.get("sessions", f"redirect_{state}")
        if isinstance(redirect_data, dict) and "value" in redirect_data:
            redirect_path = redirect_data["value"]
        else:
            redirect_path = "/"

        if redirect_path:
            await self.session_store.delete("sessions", f"redirect_{state}")

        # Validate redirect path to prevent open redirect attacks
        # Only allow relative paths (starting with /) or empty string
        if redirect_path and not (
            redirect_path.startswith("/") or redirect_path == ""
        ):
            logger.warning(f"Invalid redirect path attempted: {redirect_path}")
            redirect_path = "/"

        # Use provider's helper methods to exchange code and verify user
        try:
            # Exchange code for access token
            access_token = await provider.exchange_code_for_token(code)

            # Get user info (normalized for web)
            user_data = await provider.get_user_info_for_web(access_token)
            username = user_data.get("username")

            # Check access and admin permissions
            has_access = await provider.verify_user_access(access_token)
            if not has_access:
                logger.warning(f"User {username} does not have required access")
                return HTMLResponse("Access denied", status_code=403)

            # Check admin access
            has_admin = await provider.verify_user_admin_access(access_token)

            # Create session
            session_id = secrets.token_urlsafe(32)
            current_time = time.time()
            session_data = {
                "username": username,
                "access_token": access_token,
                "provider": provider.provider_name,
                "has_admin": has_admin,
                "created_at": current_time,
                "expires_at": current_time + self.session_timeout,
                "last_verified": current_time,
            }
            await self.session_store.set("sessions", session_id, session_data)

            # Set session cookie and redirect
            response = RedirectResponse(url=redirect_path, status_code=303)
            # Secure cookie settings (adjust as needed for HTTPS)
            response.set_cookie(
                "web_session",
                session_id,
                max_age=self.session_timeout,
                httponly=True,
                secure=provider.base_url.startswith("https://"),
                samesite="lax",
            )

            logger.info(
                f"Created web session for {username} via {provider.display_name} "
                f"(has_admin={has_admin}) redirect={redirect_path}"
            )
            return response

        except Exception as e:
            logger.error(f"Error handling OAuth callback: {e}")
            return HTMLResponse(f"Error: {str(e)}", status_code=400)


def setup_shared_auth_routes(app, session_manager: WebSessionManager):
    """Setup shared authentication routes (login and logout) for all web interfaces."""

    @app.route("/login")
    async def login(request):
        """Start web login flow - show provider selection or redirect if single provider."""
        redirect_after_login = request.query_params.get("redirect", "/")
        provider_name = request.query_params.get("provider")
        
        # If auth is disabled, create a dev session automatically
        if not session_manager.require_auth:
            session_id = secrets.token_urlsafe(32)
            current_time = time.time()
            session_data = {
                "username": "dev-user",
                "access_token": None,
                "provider": "dev",
                "has_admin": True,
                "created_at": current_time,
                "expires_at": current_time + session_manager.session_timeout,
                "last_verified": current_time,
            }
            await session_manager.session_store.set("sessions", session_id, session_data)
            
            response = RedirectResponse(url=redirect_after_login, status_code=303)
            response.set_cookie(
                "web_session",
                session_id,
                max_age=session_manager.session_timeout,
                httponly=True,
                secure=False,  # Dev mode, not using HTTPS typically
                samesite="lax",
            )
            logger.info(f"Created dev session for dev-user (auth disabled)")
            return response
        
        # If no provider configured, error
        if not session_manager.oauth_provider:
            return HTMLResponse("No OAuth provider configured", status_code=500)
        
        # Start OAuth flow with the configured provider
        oauth_url, state = await session_manager.create_oauth_login_url(
            session_manager.oauth_provider, redirect_after_login=redirect_after_login
        )

        # Store state in session storage with expiration
        current_time = time.time()
        expires_at = current_time + 600  # 10 minutes
        await session_manager.session_store.set(
            "sessions",
            f"state_{state}",
            {"value": True, "expires_at": expires_at},
        )

        # Redirect to OAuth provider
        return RedirectResponse(url=oauth_url, status_code=303)

    @app.route("/logout")
    async def logout(request):
        """Logout from web interface."""
        session_id = request.cookies.get("web_session")
        if session_id:
            await session_manager.session_store.delete("sessions", session_id)

        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie("web_session")
        return response



