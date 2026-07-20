"""Conservative browser metadata persistence policy.

This mirrors apps/desktop/src/shared/browserPersistence.ts. URL userinfo,
fragments, OAuth/SAML assertions, capability/session credentials, and signed
download parameters must not cross the Electron-to-Workspace boundary.
Ordinary query parameters remain valid.
"""

import re
from urllib.parse import parse_qsl, urlsplit

BROWSER_TAB_LIMIT = 50
BROWSER_URL_LIMIT = 8192
BROWSER_TITLE_LIMIT = 512

_SENSITIVE_PARAMETER_NAMES = {
    "accesstoken", "assertion", "authorization", "authtoken", "bearertoken",
    "capability", "capabilitytoken", "code", "credential", "idtoken", "jwt",
    "macaroon", "oauthcode", "oauthstate", "oauthtoken", "oauthverifier",
    "password", "refreshtoken", "relaystate", "samlrequest", "samlresponse",
    "secret", "session", "sessionid", "sessionkey", "sid", "sig", "signature",
    "signedurl", "state", "ticket", "token",
}


def is_sensitive_browser_parameter(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return (
        normalized in _SENSITIVE_PARAMETER_NAMES
        or normalized.startswith(("xamz", "xgoog"))
        or normalized.endswith(
            ("password", "secret", "token", "credential", "assertion", "signature")
        )
    )


def safe_browser_url(value: object, *, allow_blank: bool) -> bool:
    if value == "about:blank":
        return allow_blank
    if not isinstance(value, str) or len(value) > BROWSER_URL_LIMIT:
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
        and not any(is_sensitive_browser_parameter(key) for key, _ in parse_qsl(parsed.query))
    )
