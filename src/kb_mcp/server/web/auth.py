"""Shared authentication for web interfaces (admin and web) - server package."""

from __future__ import annotations

import logging
import secrets
import time
from starlette.responses import HTMLResponse, RedirectResponse

from ..session_store import SessionStore
from ..oauth import BaseOAuthProvider

logger = logging.getLogger(__name__)


class WebSessionManager:
    """Manages browser sessions for web interfaces."""

    def __init__(self, oauth_provider: BaseOAuthProvider | None):
        """Initialize with a single OAuth provider (or None if auth disabled)."""
        from ...config import get_auth_config

        self.oauth_provider = oauth_provider
        auth_config = get_auth_config()

        # Initialize session store for persistence (local at data/web_sessions.json or firestore)
        # Lazy loading: data loads on first access
        self.session_store = SessionStore(collection_name="web_sessions")

        # Session timeout in seconds (default: 24 hours)
        self.session_timeout = auth_config['session_timeout']
        # Re-verification interval in seconds (default: 1 hour)
        self.reverify_interval = auth_config['reverify_interval']
        # OAuth state timeout in seconds (default: 10 minutes)
        self.oauth_state_timeout = auth_config['oauth_state_timeout']

        # Flag to disable authentication for evelopment or localhost only bindings
        disable_auth = auth_config['disable_auth']
        self.require_auth = not disable_auth
        if disable_auth:
            logger.warning(
                "AUTHENTICATION DISABLED - For development/binding to localhost only! "
                "Remove DISABLE_AUTH or set DISABLE_AUTH=false for production."
            )
        #logger.info(
        #    f"Web session timeout: {self.session_timeout} seconds "
        #    f"({self.session_timeout / 3600:.1f} hours)"
        #)
        #logger.info(
        #    f"Web re-verification interval: {self.reverify_interval} seconds "
        #    f"({self.reverify_interval / 3600:.1f} hours)"
        #)

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

        # Periodic re-verification with provider (or forced for admin pages)
        last_verified = session_data.get("last_verified", 0)
        should_reverify = force_reverify or (
            current_time - last_verified > self.reverify_interval
        )

        if should_reverify:
            provider_token_data = session_data.get("provider_token_data")
            provider_name = session_data.get("provider", "github")  # Default to github for backward compatibility

            if not provider_token_data:
                # No provider token data stored (old session), invalidate
                logger.warning(
                    f"No provider token data in session for user: {session_data.get('username')}"
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

            # Extract main access token from dict
            access_token = provider_token_data.get("access_token")
            if not access_token:
                logger.warning(
                    f"No access_token in provider_token_data for user: {session_data.get('username')}"
                )
                await self.session_store.delete("sessions", session_id)
                return None

            try:
                # Re-verify user with provider
                user_data = await self.oauth_provider.get_user_info(access_token)
                username = self.oauth_provider._extract_username(user_data)

                # Re-verify access and admin permissions (pass full provider_token_data dict)
                has_access = await self.oauth_provider.verify_user_access(provider_token_data)
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
                <strong>Development or Localhost Mode:</strong> Authentication is disabled (DISABLE_AUTH=true).
            </div>
            """
        return ""

    async def create_oauth_login_url(
        self, provider: BaseOAuthProvider, redirect_after_login: str = "/"
    ) -> tuple[str, str]:
        """Create OAuth login URL for a specific provider."""
        state = secrets.token_urlsafe(32)
        # Store state data with expiration
        current_time = time.time()
        expires_at = current_time + self.oauth_state_timeout
        await self.session_store.set(
            "web_oauth_states",
            state,
            {
                "redirect_path": redirect_after_login,
                "provider": provider.provider_name,
                "expires_at": expires_at,
            },
        )

        redirect_uri = f"{provider.base_url}{provider.callback_path}"
        oauth_url = await provider.create_authorize_url(state, redirect_uri)
        return oauth_url, state

    async def handle_oauth_callback(self, provider: BaseOAuthProvider, code: str, state: str):
        """Handle OAuth callback and create session."""
        # Load consolidated state data
        state_data = await self.session_store.get("web_oauth_states", state)
        if not state_data or not isinstance(state_data, dict):
            return HTMLResponse("Invalid state", status_code=400)

        # Check expiration
        expires_at = state_data.get("expires_at")
        if expires_at and time.time() > expires_at:
            await self.session_store.delete("web_oauth_states", state)
            return HTMLResponse("State expired", status_code=400)

        # Extract redirect path from consolidated state data
        redirect_path = state_data.get("redirect_path", "/")

        # Clean up used state
        await self.session_store.delete("web_oauth_states", state)

        # Validate redirect path to prevent open redirect attacks
        # Only allow relative paths (starting with /) or empty string
        if redirect_path and not (
            redirect_path.startswith("/") or redirect_path == ""
        ):
            logger.warning(f"Invalid redirect path attempted: {redirect_path}")
            redirect_path = "/"

        # Use provider's helper methods to exchange code and verify user
        try:
            # Exchange code for provider token data (dict with 'access_token' and optional extras)
            provider_token_data = await provider.exchange_code_for_token(code)

            # Extract main access token from dict
            access_token = provider_token_data.get("access_token")
            if not access_token:
                logger.error("No access_token in provider token data")
                return HTMLResponse("Authentication failed", status_code=500)

            # Get user info and extract username
            user_data = await provider.get_user_info(access_token)
            username = provider._extract_username(user_data)

            # Check access and admin permissions (pass full provider_token_data dict)
            has_access = await provider.verify_user_access(provider_token_data)
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
                "provider_token_data": provider_token_data,  # Store full dict (includes access_token and extras)
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
            # Cookie max_age uses session_timeout from config (WEB_SESSION_TIMEOUT)
            response.set_cookie(
                "web_session",
                session_id,
                max_age=self.session_timeout,  # From WEB_SESSION_TIMEOUT config
                httponly=True,
                secure=provider.base_url.startswith("https://"),
                samesite="lax",
            )

            logger.info(
                f"Created web session for {username} via {provider.provider_name.capitalize()} "
                f"(has_admin={has_admin}) redirect={redirect_path}"
            )
            return response

        except Exception as e:
            logger.error(f"Error handling OAuth callback: {e}")
            return HTMLResponse(f"Error: {str(e)}", status_code=400)


def setup_shared_auth_routes(app, session_manager: WebSessionManager):
    """Setup shared authentication routes (login and logout) for all web interfaces.
    
    Note: The OAuth callback route (/oauth/callback) is defined in server.py since it
    handles both MCP and web OAuth flows, not just web authentication.
    
    Args:
        app: Starlette application instance
        session_manager: WebSessionManager instance
    """

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
            # Cookie max_age uses session_timeout from config (WEB_SESSION_TIMEOUT)
            response.set_cookie(
                "web_session",
                session_id,
                max_age=session_manager.session_timeout,  # From WEB_SESSION_TIMEOUT config
                httponly=True,
                secure=False,  # Dev mode, not using HTTPS typically
                samesite="lax",
            )
            logger.info(f"Created dev session for dev-user (auth disabled)")
            return response
        
        # If no provider configured, error
        if not session_manager.oauth_provider or session_manager.oauth_provider.provider_name == "api-key":
            return HTMLResponse(f"""<h1>No OAuth provider configured</h1>
            <p>Authentification is requiered but no OAuth provider is configured.</p>
            <p>To enable OAuth, set the GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET or GLOBUS_CLIENT_ID and GLOBUS_CLIENT_SECRET environment variables.</p>
            <p>To disable authentication for development or localhost only bindings, set DISABLE_AUTH=true in the environment variables.</p>""", 
            status_code=500)

        # Start OAuth flow with the configured provider
        # (state data is stored inside create_oauth_login_url)
        oauth_url, state = await session_manager.create_oauth_login_url(
            session_manager.oauth_provider, redirect_after_login=redirect_after_login
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
