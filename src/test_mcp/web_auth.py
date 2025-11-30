"""Shared authentication for web interfaces (admin and web)."""

import logging
import os
import secrets
import time
from urllib.parse import quote, unquote
from starlette.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger(__name__)


class WebSessionManager:
    """Manages browser sessions for web interfaces."""

    def __init__(self, oauth_provider):
        self.oauth_provider = oauth_provider
        # Store browser sessions (in production, use Redis or similar)
        self.sessions = {}
        # Session timeout in seconds (default: 24 hours)
        self.session_timeout = int(os.getenv("WEB_SESSION_TIMEOUT", "86400"))
        # Re-verification interval in seconds (default: 1 hour)
        self.reverify_interval = int(os.getenv("WEB_REVERIFY_INTERVAL", "3600"))
        # Flag to disable authentication (for local development only)
        self.require_auth = os.getenv("REQUIRE_WEB_AUTH", "true").lower() == "true"
        if not self.require_auth:
            logger.warning("WEB AUTHENTICATION DISABLED - For development only! Set REQUIRE_WEB_AUTH=true for production.")
        logger.info(f"Web session timeout: {self.session_timeout} seconds ({self.session_timeout / 3600:.1f} hours)")
        logger.info(f"Web re-verification interval: {self.reverify_interval} seconds ({self.reverify_interval / 3600:.1f} hours)")

    async def get_session_data(self, request, force_reverify=False):
        """Get full session data from browser session cookie with re-verification.

        Args:
            request: The request object
            force_reverify: If True, always re-verify with GitHub (used for admin pages)
        """
        # Bypass authentication if disabled
        if not self.require_auth:
            return {'username': 'dev-user', 'has_admin': True}

        session_id = request.cookies.get("web_session")
        if not session_id:
            return None

        session_data = self.sessions.get(session_id)
        if not session_data:
            return None

        current_time = time.time()

        # Check if session has expired
        expires_at = session_data.get('expires_at')
        if expires_at and current_time > expires_at:
            # Session expired, clean it up
            logger.info(f"Session expired for user: {session_data.get('username')}")
            self.sessions.pop(session_id, None)
            return None

        # Sliding expiration - extend session on each request
        session_data['expires_at'] = current_time + self.session_timeout

        # Periodic re-verification with GitHub (or forced for admin pages)
        last_verified = session_data.get('last_verified', 0)
        should_reverify = force_reverify or (current_time - last_verified > self.reverify_interval)

        if should_reverify:
            github_token = session_data.get('github_token')
            if not github_token:
                # No GitHub token stored (old session), invalidate
                logger.warning(f"No GitHub token in session for user: {session_data.get('username')}")
                self.sessions.pop(session_id, None)
                return None

            try:
                # Re-verify GitHub user
                user_data = await self.oauth_provider.get_github_user(github_token)
                username = user_data.get('login')

                # Re-verify repository access and admin permissions
                has_access = True
                has_admin = False

                if self.oauth_provider.required_repo:
                    has_access = await self.oauth_provider.verify_repo_access(
                        github_token, require_admin=False
                    )
                    if not has_access:
                        logger.warning(f"User {username} no longer has access to {self.oauth_provider.required_repo}")
                        self.sessions.pop(session_id, None)
                        return None

                    has_admin = await self.oauth_provider.verify_repo_access(
                        github_token, require_admin=True
                    )

                # Update session with re-verified data
                session_data['username'] = username
                session_data['has_admin'] = has_admin
                session_data['last_verified'] = current_time
                if force_reverify:
                    logger.debug(f"Re-verified admin session for {username} (has_admin={has_admin})")
                else:
                    logger.info(f"Re-verified session for {username} (has_admin={has_admin})")

            except Exception as e:
                # Re-verification failed, invalidate session
                logger.error(f"Session re-verification failed for {session_data.get('username')}: {e}")
                self.sessions.pop(session_id, None)
                return None

        return session_data

    async def get_session_username(self, request, force_reverify=False):
        """Get username from browser session cookie.

        Args:
            request: The request object
            force_reverify: If True, always re-verify with GitHub (used for admin pages)
        """
        session_data = await self.get_session_data(request, force_reverify=force_reverify)
        if not session_data:
            return None
        return session_data.get('username')

    async def has_admin_access(self, request, force_reverify=False):
        """Check if session has admin access.

        Args:
            request: The request object
            force_reverify: If True, always re-verify with GitHub (used for admin pages)
        """
        session_data = await self.get_session_data(request, force_reverify=force_reverify)
        if not session_data:
            return False
        return session_data.get('has_admin', False)

    def get_auth_warning_html(self) -> str:
        """Get HTML warning banner if authentication is disabled."""
        if not self.require_auth:
            return """
            <div style="background: #fff3cd; border: 1px solid #ffc107; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                <strong>Development Mode:</strong> Web authentication is disabled (REQUIRE_WEB_AUTH=false).
                Enable authentication for production deployments.
            </div>
            """
        return ""

    def create_oauth_login_url(self, redirect_after_login: str = "/") -> tuple[str, str]:
        """
        Create GitHub OAuth login URL.

        Args:
            redirect_after_login: Path to redirect to after successful login

        Returns:
            Tuple of (github_auth_url, state)
        """
        state = secrets.token_urlsafe(32)
        # Store redirect path in state mapping
        self.sessions[f"redirect_{state}"] = redirect_after_login

        github_auth_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={self.oauth_provider.github_client_id}"
            f"&redirect_uri={self.oauth_provider.base_url}/oauth/github/callback"
            f"&state={state}"
            f"&scope=read:user repo"
        )
        return github_auth_url, state

    async def handle_oauth_callback(self, code: str, state: str):
        """
        Handle OAuth callback and create session.

        Args:
            code: GitHub authorization code
            state: State parameter from OAuth flow

        Returns:
            RedirectResponse with session cookie or error HTMLResponse
        """
        # Get redirect path from state
        redirect_path = self.sessions.pop(f"redirect_{state}", "/")

        # Verify state was valid
        if not self.sessions.pop(f"state_{state}", None):
            return HTMLResponse("Invalid state", status_code=400)

        # Use oauth_provider's helper methods to get GitHub token and verify user
        try:
            # Exchange code for GitHub access token
            github_token = await self.oauth_provider.exchange_github_code(code)

            # Get GitHub user info
            user_data = await self.oauth_provider.get_github_user(github_token)
            username = user_data.get('login')

            # Check repository access and admin permissions
            has_access = True
            has_admin = False

            if self.oauth_provider.required_repo:
                # Check basic access
                has_access = await self.oauth_provider.verify_repo_access(
                    github_token, require_admin=False
                )
                if not has_access:
                    return HTMLResponse(
                        f"Access denied: You need access to {self.oauth_provider.required_repo}",
                        status_code=403
                    )

                # Check admin permissions (but don't deny access if not admin)
                has_admin = await self.oauth_provider.verify_repo_access(
                    github_token, require_admin=True
                )
        except Exception as e:
            logger.error(f"OAuth error: {e}")
            return HTMLResponse(f"OAuth error: {str(e)}", status_code=400)

        # Create browser session with permission data, expiration, and GitHub token
        session_id = secrets.token_urlsafe(32)
        current_time = time.time()
        expires_at = current_time + self.session_timeout
        self.sessions[session_id] = {
            'username': username,
            'has_admin': has_admin,
            'expires_at': expires_at,
            'github_token': github_token,
            'last_verified': current_time
        }
        logger.info(f"Web login: {username} (has_admin={has_admin}, expires in {self.session_timeout / 3600:.1f} hours)")

        # Set cookie and redirect
        response = RedirectResponse(url=redirect_path, status_code=303)
        response.set_cookie(
            key="web_session",
            value=session_id,
            httponly=True,
            secure=True,
            samesite="lax"
        )
        return response

    def logout(self, request):
        """Logout from web interface."""
        session_id = request.cookies.get("web_session")
        if session_id:
            self.sessions.pop(session_id, None)

        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie("web_session")
        return response


def setup_shared_auth_routes(app, session_manager: WebSessionManager):
    """Setup shared authentication routes (login and logout) for all web interfaces."""

    @app.route("/login")
    async def login(request):
        """Unified login endpoint - redirects to GitHub OAuth."""
        # Get redirect parameter (where to go after successful login)
        redirect_after = request.query_params.get("redirect", "/")

        # Create OAuth login URL with redirect path stored in state
        github_auth_url, state = session_manager.create_oauth_login_url(redirect_after)

        # Store state to verify callback
        session_manager.sessions[f"state_{state}"] = True

        return RedirectResponse(url=github_auth_url)

    @app.route("/logout")
    async def logout(request):
        """Unified logout endpoint - clears session and redirects to home."""
        return session_manager.logout(request)

    async def handle_unified_callback(code: str, state: str):
        """Handle unified OAuth callback (called from main OAuth callback)."""
        return await session_manager.handle_oauth_callback(code, state)

    return handle_unified_callback
