"""Log in to a Fermilab SSO-protected site with a username and password.

Fermilab's SSO is PingFederate at pingprod.fnal.gov. A protected site (DocDB's
/cgi-bin/sso/, the internal wiki's /mellon/ login) redirects there, the form
adapter takes the credentials, and the identity provider posts a SAML
assertion back to the site, which sets its own session cookies. The returned
requests.Session carries those cookies, so later requests to the site are
authenticated.

Shared by the DocDB and MediaWiki importers; both use MU2E_DOCDB_USERNAME and
MU2E_DOCDB_PASSWORD (the same Fermilab account).
"""

import logging
import os
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IDP_BASE = "https://pingprod.fnal.gov"


def credentials_from_env() -> tuple:
    """(username, password) from MU2E_DOCDB_USERNAME / MU2E_DOCDB_PASSWORD; raises if either is unset."""
    missing = [v for v in ("MU2E_DOCDB_USERNAME", "MU2E_DOCDB_PASSWORD") if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Fermilab SSO login needs {', '.join(missing)} (in .env.local)")
    return os.getenv("MU2E_DOCDB_USERNAME"), os.getenv("MU2E_DOCDB_PASSWORD")


def sso_session(start_url: str, username: Optional[str] = None, password: Optional[str] = None,
                timeout: float = 60.0, session: Optional[requests.Session] = None) -> requests.Session:
    """A session logged in to the site behind `start_url` through Fermilab SSO.

    `start_url` is any page of the site that redirects to the identity
    provider when not logged in. Credentials default to the environment
    (credentials_from_env). Raises RuntimeError when the login form cannot be
    found.
    """
    if username is None or password is None:
        username, password = credentials_from_env()
    session = session or requests.Session()

    # Step 1: the site redirects to PingFederate.
    response = session.get(start_url, timeout=timeout)

    # Step 2: choose the username/password form adapter.
    form = BeautifulSoup(response.text, "html.parser").find("form")
    if not form:
        session.close()
        raise RuntimeError(f"No identity-provider form after {start_url} (already logged in, or the site changed)")
    response = session.get(
        urljoin(IDP_BASE, form["action"]),
        params={"pfidpadapterid": "ad..FormBased", "rememberChoice": "false"},
        timeout=timeout,
    )

    # Step 3: submit the credentials.
    login_form = BeautifulSoup(response.text, "html.parser").find("form")
    if not login_form:
        session.close()
        raise RuntimeError("Could not find the login form on the Fermilab SSO page")
    response = session.post(
        urljoin(IDP_BASE, login_form["action"]),
        data={"pf.username": username, "pf.pass": password, "pf.ok": "clicked",
              "pf.adapterId": "FormBased", "pf.cancel": ""},
        timeout=timeout,
    )

    # Step 4: forward the SAML assertion to the site.
    saml_form = BeautifulSoup(response.text, "html.parser").find("form")
    if saml_form:
        saml_response = saml_form.find("input", {"name": "SAMLResponse"})
        relay_state = saml_form.find("input", {"name": "RelayState"})
        if saml_response and relay_state:
            session.post(saml_form["action"],
                         data={"RelayState": relay_state["value"], "SAMLResponse": saml_response["value"]},
                         timeout=timeout)
    else:
        logger.warning("No SAML form after the SSO login -- the login may have failed")
    return session
