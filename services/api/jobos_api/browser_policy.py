"""Conservative browser metadata persistence policy.

This mirrors apps/desktop/src/shared/browserPersistence.ts. URL userinfo,
fragments, OAuth/SAML assertions, capability/session credentials, and signed
download parameters must not cross the Electron-to-Workspace boundary.
Ordinary query parameters remain valid.
"""

import re
from ipaddress import IPv6Address
from urllib.parse import parse_qsl, unquote, urlsplit

BROWSER_TAB_LIMIT = 50
BROWSER_URL_LIMIT = 8192
BROWSER_TITLE_LIMIT = 512

_SENSITIVE_PARAMETER_NAMES = {
    "accesstoken", "apikey", "assertion", "authorization", "authorizationcode",
    "authcode", "authtoken", "bearertoken", "capability", "capabilitytoken",
    "code", "codeverifier", "credential", "idtoken", "jsessionid", "jwt",
    "macaroon", "oauthcode", "oauthstate", "oauthtoken", "oauthverifier",
    "password", "phpsessid", "refreshtoken", "relaystate", "samlart",
    "samlrequest", "samlresponse", "secret", "session", "sessionid",
    "sessionkey", "sid", "sig", "signature", "signedurl", "state", "ticket",
    "token",
}


def is_sensitive_browser_parameter(name: str) -> bool:
    decoded = name
    for _attempt in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    normalized = re.sub(r"[^a-z0-9]", "", decoded.lower())
    return (
        normalized in _SENSITIVE_PARAMETER_NAMES
        or normalized.startswith(("xamz", "xgoog"))
        or normalized.endswith(
            ("password", "secret", "token", "credential", "assertion", "signature")
        )
    )


def _has_sensitive_path_parameter(path: str) -> bool:
    decoded_path = path
    for _attempt in range(3):
        next_value = unquote(decoded_path)
        if next_value == decoded_path:
            break
        decoded_path = next_value
    for segment in decoded_path.split("/"):
        parts = segment.split(";")
        for parameter in parts[1:]:
            name = parameter.split("=", maxsplit=1)[0]
            if is_sensitive_browser_parameter(name):
                return True
    return False


def safe_browser_url(value: object, *, allow_blank: bool) -> bool:
    if value == "about:blank":
        return allow_blank
    if not isinstance(value, str) or len(value) > BROWSER_URL_LIMIT:
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing these properties performs validation that urlsplit defers,
        # including bracketed hosts and numeric port range checks.
        port = parsed.port
        username = parsed.username
        password = parsed.password
        if parsed.scheme not in ("http", "https") or not hostname:
            return False
        if username or password or parsed.fragment:
            return False
        if any(character.isspace() or ord(character) < 32 for character in value):
            return False
        if "\\" in parsed.netloc or port is not None and not 0 < port <= 65535:
            return False
        if ":" in hostname:
            IPv6Address(hostname)
        else:
            ascii_hostname = hostname.rstrip(".").encode("idna").decode("ascii")
            if not ascii_hostname or len(ascii_hostname) > 253:
                return False
            if any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[a-zA-Z0-9_-]+", label) is None
                for label in ascii_hostname.split(".")
            ):
                return False
        if _has_sensitive_path_parameter(parsed.path):
            return False
        return not any(
            is_sensitive_browser_parameter(key)
            for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        )
    except (TypeError, ValueError, UnicodeError, OverflowError):
        return False
