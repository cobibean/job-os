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
BROWSER_SAFE_TITLE_FALLBACK = "Protected page"

_SENSITIVE_PARAMETER_NAMES = {
    "accesstoken",
    "apikey",
    "assertion",
    "authorization",
    "authorizationcode",
    "authcode",
    "authtoken",
    "bearertoken",
    "capability",
    "capabilitytoken",
    "code",
    "codeverifier",
    "credential",
    "idtoken",
    "jsessionid",
    "jwt",
    "macaroon",
    "oauthcode",
    "oauthstate",
    "oauthtoken",
    "oauthverifier",
    "password",
    "phpsessid",
    "refreshtoken",
    "relaystate",
    "samlart",
    "samlrequest",
    "samlresponse",
    "secret",
    "session",
    "sessionid",
    "sessionkey",
    "sid",
    "sig",
    "signature",
    "signedurl",
    "state",
    "ticket",
    "token",
}

_TITLE_CREDENTIAL_CARRIER_NAMES = (
    "accesskey",
    "accesstoken",
    "apikey",
    "assertion",
    "authorization",
    "authorizationcode",
    "authcode",
    "awsaccesskeyid",
    "awssecretaccesskey",
    "authtoken",
    "bearertoken",
    "capability",
    "capabilitytoken",
    "codeverifier",
    "credential",
    "idtoken",
    "jsessionid",
    "jwt",
    "macaroon",
    "oauthcode",
    "oauthstate",
    "oauthtoken",
    "oauthverifier",
    "password",
    "phpsessid",
    "privatekey",
    "refreshtoken",
    "relaystate",
    "samlart",
    "samlrequest",
    "samlresponse",
    "sessionid",
    "sessionkey",
    "signature",
    "signedurl",
    "ticket",
    "xamzcredential",
    "xamzsignature",
    "xgoogsignature",
)
_TITLE_EQUALS_ONLY_CARRIER_NAMES = (
    "code",
    "secret",
    "session",
    "sid",
    "sig",
    "state",
    "token",
)


def _title_carrier_pattern(name: str, delimiter: str) -> re.Pattern[str]:
    flexible_name = r"[\s_.-]*".join(re.escape(character) for character in name)
    return re.compile(
        rf"(?:^|[^a-z0-9]){flexible_name}\s*{delimiter}\s*"
        rf'(?:"[^"]+"|\'[^\']+\'|\S+)',
        re.IGNORECASE,
    )


_TITLE_CREDENTIAL_PATTERNS = (
    *(_title_carrier_pattern(name, r"(?:=|:)") for name in _TITLE_CREDENTIAL_CARRIER_NAMES),
    *(_title_carrier_pattern(name, "=") for name in _TITLE_EQUALS_ONLY_CARRIER_NAMES),
)


def tolerant_percent_decode(value: str, *, limit: int = BROWSER_URL_LIMIT) -> str:
    decoded = value[:limit]
    for _attempt in range(3):
        next_value = re.sub(
            r"%(?![0-9a-f]{2})[^\s%]{0,2}",
            " ",
            unquote(decoded, errors="replace"),
            flags=re.IGNORECASE,
        )[:limit]
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def is_sensitive_browser_parameter(name: str) -> bool:
    decoded = tolerant_percent_decode(name)
    normalized = re.sub(r"[^a-z0-9]", "", decoded.lower())
    return (
        normalized in _SENSITIVE_PARAMETER_NAMES
        or normalized.startswith(("xamz", "xgoog"))
        or normalized.endswith(
            ("password", "secret", "token", "credential", "assertion", "signature")
        )
    )


def browser_title_contains_credentials(value: object) -> bool:
    if not isinstance(value, str):
        return False
    decoded = tolerant_percent_decode(value, limit=BROWSER_TITLE_LIMIT * 4)
    return any(pattern.search(decoded) for pattern in _TITLE_CREDENTIAL_PATTERNS)


def sanitize_browser_title(value: str) -> str:
    return BROWSER_SAFE_TITLE_FALLBACK if browser_title_contains_credentials(value) else value


def _has_sensitive_path_parameter(path: str) -> bool:
    decoded_path = tolerant_percent_decode(path)
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
