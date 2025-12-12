"""Shared authentication for web interfaces (admin and web) - server package."""

from __future__ import annotations

import logging
import os
import secrets
import time
from starlette.responses import HTMLResponse, RedirectResponse

from .session_store import SessionStore

logger = logging.getLogger(__name__)


class WebSessionManager:
    """Manages browser sessions for web interfaces."""

    def __init__(self, oauth_provider):
        from ..config import get_web_session_config

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
            github_token = session_data.get("github_token")
            if not github_token:
                # No GitHub token stored (old session), invalidate
                logger.warning(
                    f"No GitHub token in session for user: {session_data.get('username')}"
                )
                await self.session_store.delete("sessions", session_id)
                return None

            try:
                # Re-verify GitHub user
                user_data = await self.oauth_provider.get_github_user(github_token)
                username = user_data.get("login")

                # Re-verify repository access and admin permissions
                has_access = True
                has_admin = False

                if self.oauth_provider.required_repo:
                    has_access = await self.oauth_provider.verify_repo_access(
                        github_token, require_admin=False
                    )
                    if not has_access:
                        logger.warning(
                            f"User {username} no longer has access to "
                            f"{self.oauth_provider.required_repo}"
                        )
                        await self.session_store.delete("sessions", session_id)
                        return None

                    has_admin = await self.oauth_provider.verify_repo_access(
                        github_token, require_admin=True
                    )

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
        self, redirect_after_login: str = "/"
    ) -> tuple[str, str]:
        """Create GitHub OAuth login URL."""
        state = secrets.token_urlsafe(32)
        # Store redirect path in state mapping with expiration (OAuth flows timeout after 10 minutes)
        current_time = time.time()
        expires_at = current_time + 600  # 10 minutes
        await self.session_store.set(
            "sessions",
            f"redirect_{state}",
            {"value": redirect_after_login, "expires_at": expires_at},
        )

        github_auth_url = (
            "https://github.com/login/oauth/authorize"
            f"?client_id={self.oauth_provider.github_client_id}"
            f"&redirect_uri={self.oauth_provider.base_url}/oauth/github/callback"
            f"&state={state}"
            f"&scope=read:user repo"
        )
        return github_auth_url, state

    async def handle_oauth_callback(self, code: str, state: str):
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

        # Use oauth_provider's helper methods to get GitHub token and verify user
        try:
            # Exchange code for GitHub access token
            github_token = await self.oauth_provider.exchange_github_code(code)

            # Get GitHub user info
            user_data = await self.oauth_provider.get_github_user(github_token)
            username = user_data.get("login")

            # Check repository access and admin permissions
            has_access = True
            has_admin = False

            if self.oauth_provider.required_repo:
                has_access = await self.oauth_provider.verify_repo_access(
                    github_token, require_admin=False
                )
                if not has_access:
                    logger.warning(
                        f"User {username} does not have access to "
                        f"{self.oauth_provider.required_repo}"
                    )
                    return HTMLResponse("Access denied", status_code=403)

                has_admin = await self.oauth_provider.verify_repo_access(
                    github_token, require_admin=True
                )

            # Create session
            session_id = secrets.token_urlsafe(32)
            current_time = time.time()
            session_data = {
                "username": username,
                "github_token": github_token,
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
                secure=self.oauth_provider.base_url.startswith("https://"),
                samesite="lax",
            )

            logger.info(
                f"Created web session for {username} (has_admin={has_admin}) "
                f"redirect={redirect_path}"
            )
            return response

        except Exception as e:
            logger.error(f"Error handling OAuth callback: {e}")
            return HTMLResponse(f"Error: {str(e)}", status_code=400)


def setup_shared_auth_routes(app, session_manager: WebSessionManager):
    """Setup shared authentication routes (login and logout) for all web interfaces."""

    @app.route("/login")
    async def login(request):
        """Start web login flow."""
        redirect_after_login = request.query_params.get("redirect", "/")
        
        # If auth is disabled, create a dev session automatically
        if not session_manager.require_auth:
            session_id = secrets.token_urlsafe(32)
            current_time = time.time()
            session_data = {
                "username": "dev-user",
                "github_token": None,
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
        
        # Normal OAuth flow
        github_auth_url, state = await session_manager.create_oauth_login_url(
            redirect_after_login=redirect_after_login
        )

        # Store state in session storage with expiration
        current_time = time.time()
        expires_at = current_time + 600  # 10 minutes
        await session_manager.session_store.set(
            "sessions",
            f"state_{state}",
            {"value": True, "expires_at": expires_at},
        )

        # Redirect to GitHub OAuth
        return RedirectResponse(url=github_auth_url, status_code=303)

    @app.route("/logout")
    async def logout(request):
        """Logout from web interface."""
        session_id = request.cookies.get("web_session")
        if session_id:
            await session_manager.session_store.delete("sessions", session_id)

        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie("web_session")
        return response

    async def unified_github_callback(code: str, state: str):
        """Unified callback handler for admin and web interfaces."""
        return await session_manager.handle_oauth_callback(code, state)

    return unified_github_callback


