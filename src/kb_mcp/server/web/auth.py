"""Shared authentication for web interfaces (admin and web) - server package."""

from __future__ import annotations

import hashlib
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

        # Whether the web UI requires login. Resolved from WEB_REQUIRE_AUTH if
        # set, otherwise from the blanket DISABLE_AUTH - see
        # config.get_auth_config. The web UI binds to loopback by default
        # (WEB_HOST), which is what makes running it unauthenticated tolerable.
        # Password gate for the write/administrative pages. Independent of
        # require_auth: the web UI can be open for browsing on localhost while
        # deletes, uploads and API-key management still need the password.
        self._admin_password = auth_config['admin_password']
        self._admin_password_hash = auth_config['admin_password_hash']
        self.admin_password_configured = bool(
            self._admin_password or self._admin_password_hash
        )

        # See config.get_auth_config: serve the browsable pages without a
        # session, keeping the administrative ones behind the admin password.
        self.public_mode = auth_config['web_public_mode']

        self.require_auth = auth_config['web_require_auth']
        if not self.require_auth:
            logger.warning(
                "WEB AUTHENTICATION DISABLED - the web UI performs no login checks. "
                "Only safe while it is bound to localhost (WEB_HOST=127.0.0.1). "
                "Set WEB_REQUIRE_AUTH=true before exposing it on a network interface."
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

    # --- Admin password gate -------------------------------------------------
    #
    # Guards the pages that change state or hand out credentials (uploads,
    # deletes, re-chunking, API key management) while the rest of the web UI
    # stays browsable. This is a stopgap for the localhost deployment, where
    # WEB_REQUIRE_AUTH is off and every visitor would otherwise be an admin;
    # it is not a replacement for real per-user authentication.

    def verify_admin_password(self, password: str) -> bool:
        """Check a submitted password against ADMIN_PASSWORD/ADMIN_PASSWORD_HASH."""
        if not password:
            return False
        if self._admin_password_hash:
            digest = hashlib.sha256(password.encode()).hexdigest()
            return secrets.compare_digest(digest, self._admin_password_hash.strip().lower())
        if self._admin_password:
            return secrets.compare_digest(password, self._admin_password)
        return False

    async def start_admin_session(self, response) -> None:
        """Log the browser in as an admin.

        Creates an ordinary web session with has_admin=True - the same shape
        the OAuth callback produces - rather than a parallel admin-only
        cookie, so every existing session consumer (get_session_username,
        has_admin_access, the nav, the page handlers) sees a logged-in user
        without special-casing the password path. When OAuth is reintroduced
        it becomes a second way to reach the same state.
        """
        session_id = secrets.token_urlsafe(32)
        current_time = time.time()
        await self.session_store.set(
            "sessions",
            session_id,
            {
                "username": "admin",
                "access_token": None,
                "provider": "password",
                "has_admin": True,
                "created_at": current_time,
                "expires_at": current_time + self.session_timeout,
                "last_verified": current_time,
            },
        )
        response.set_cookie(
            "web_session",
            session_id,
            max_age=self.session_timeout,
            httponly=True,
            secure=False,  # loopback HTTP by default
            samesite="lax",
        )

    async def end_admin_session(self, request, response) -> None:
        """Log the browser out."""
        session_id = request.cookies.get("web_session")
        if session_id:
            await self.session_store.delete("sessions", session_id)
        response.delete_cookie("web_session")

    async def is_admin_unlocked(self, request) -> bool:
        """True if this browser is logged in as an admin.

        When no password is configured there is nothing to log in to, so the
        gate is open - existing deployments are not locked out of their own
        upload and delete pages.
        """
        if not self.admin_password_configured:
            return True

        session_data = await self.get_session_data(request)
        return bool(session_data and session_data.get("has_admin"))

    def get_auth_warning_html(self) -> str:
        """Banner shown when the site is genuinely running without protection.

        In public mode the browsable pages are meant to be open and the
        administrative ones are behind the admin password, so there is nothing
        to warn about - the banner only appears when nothing is protecting the
        write pages either.
        """
        if self.require_auth:
            return ""

        if self.public_mode:
            if self.admin_password_configured:
                # Browsing is meant to be open and the write pages are behind
                # the password, so there is nothing to warn about.
                return ""
            # Public mode without a password: nobody can reach the write pages
            # at all, because there is no way to obtain an admin session.
            return """
            <div style="background: #fff3cd; border: 1px solid #ffc107; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                <strong>No admin password set:</strong> the administrative pages
                (upload, delete, statistics, logs, evaluations) cannot be reached.
                Set <code>ADMIN_PASSWORD</code> to enable them.
            </div>
            """

        return """
            <div style="background: #fff3cd; border: 1px solid #ffc107; padding: 15px; margin-bottom: 20px; border-radius: 4px;">
                <strong>Development or Localhost Mode:</strong> Authentication is disabled (DISABLE_AUTH=true).
            </div>
            """

    async def create_oauth_login_url(
        self, provider: BaseOAuthProvider, redirect_after_login: str = "/web"
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

    async def login(request):
        """Start web login flow - show provider selection or redirect if single provider."""
        redirect_after_login = request.query_params.get("redirect", "/web")
        provider_name = request.query_params.get("provider")
        
        # In public mode there is nothing to log in to here: the browsable
        # pages need no session, and admin access comes from /admin/login.
        # Send visitors there rather than minting an anonymous session that
        # would silently carry admin rights.
        if session_manager.public_mode:
            return RedirectResponse(
                url=f"/admin/login?next={redirect_after_login}", status_code=303
            )

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
    app.add_route("/login", login)

    async def logout(request):
        """Logout from web interface."""
        session_id = request.cookies.get("web_session")
        if session_id:
            await session_manager.session_store.delete("sessions", session_id)

        response = RedirectResponse(url="/web", status_code=303)
        response.delete_cookie("web_session")
        return response

    app.add_route("/logout", logout)

    async def admin_login(request):
        """Password gate for the web UI's write/administrative pages.

        GET  shows the form (with ?next= carrying the page the user wanted).
        POST checks the password and, on success, sets the admin cookie.
        """
        from html import escape as html_escape
        from . import html_templates

        next_url = request.query_params.get("next", "/admin")
        # Only allow relative paths, so ?next= cannot bounce a visitor off-site.
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/admin"

        if not session_manager.admin_password_configured:
            return HTMLResponse(
                html_templates.base_template(
                    "Admin",
                    "<div class='card'><h2>No admin password set</h2>"
                    "<p>Administrative pages are currently unprotected because "
                    "<code>ADMIN_PASSWORD</code> is not set in the environment.</p></div>",
                ),
                status_code=200,
            )

        error_html = ""
        if request.method == "POST":
            form = await request.form()
            if session_manager.verify_admin_password(form.get("password", "")):
                response = RedirectResponse(url=next_url, status_code=303)
                await session_manager.start_admin_session(response)
                logger.info("Admin password accepted from %s", request.client.host if request.client else "?")
                return response
            logger.warning(
                "Admin password rejected from %s",
                request.client.host if request.client else "?",
            )
            error_html = "<div class='error-box'>Incorrect password.</div>"

        content = f"""
        <div class="card">
            <h2>Admin access</h2>
            <p>This page requires the admin password.</p>
            {error_html}
            <form method="POST" action="/admin/login?next={html_escape(next_url, quote=True)}">
                <div class="form-group">
                    <label for="password">Password</label>
                    <input type="password" id="password" name="password" autofocus>
                </div>
                <button type="submit" class="btn">Unlock</button>
            </form>
        </div>
        """
        return HTMLResponse(
            html_templates.base_template("Admin access", content),
            status_code=401 if error_html else 200,
        )

    app.add_route("/admin/login", admin_login, methods=["GET", "POST"])

    async def admin_logout(request):
        """Drop admin access for this browser."""
        response = RedirectResponse(url="/web", status_code=303)
        await session_manager.end_admin_session(request, response)
        return response

    app.add_route("/admin/logout", admin_logout, methods=["GET", "POST"])


async def require_admin(request, session_manager):
    """Guard for write/administrative routes.

    Returns None when the request may proceed, or a response to return instead
    (a redirect to the password form for pages, a 403 for API/POST endpoints).

    Composes with the OAuth admin check: when an OAuth provider is configured
    and says the user is an admin, that is honoured without a password prompt.
    """
    if await session_manager.is_admin_unlocked(request):
        return None

    if session_manager.require_auth and await session_manager.has_admin_access(request):
        return None

    wants_html = "text/html" in request.headers.get("accept", "")
    if request.method == "GET" and wants_html:
        return RedirectResponse(
            url=f"/admin/login?next={request.url.path}", status_code=303
        )
    return HTMLResponse("Admin password required", status_code=403)
