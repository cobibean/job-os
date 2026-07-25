import re
from typing import Any

MAX_STRING = 1000
MAX_ASSISTANT_TEXT = 100_000
MAX_USER_TEXT = 12_000
MAX_SUMMARY = 500
MAX_ITEMS = 30
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|token|api[_-]?key|password|credential|secret|environment|signed[_-]?url)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:"
    r"(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+"
    r"|bearer\s+\S+"
    r"|(?:token|api[_-]?key|password|secret|credential|authorization[_-]?code)"
    r"\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)
_STANDALONE_CREDENTIAL = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"Basic\s+[A-Za-z0-9+/]{4,}={0,2}"
    r"|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{16,}"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CREDENTIAL_PATH = re.compile(r"(?:^|/)(?:\.hermes|\.ssh|mcp-tokens|auth\.json|\.env)(?:/|$)")
_SIGNED_URL = re.compile(
    r"[?&](?:x-amz-signature|signature|signed|sig|token|api[_-]?key)=[^&#]+",
    re.IGNORECASE,
)
_PATH_KEY = re.compile(r"(?:^|[_-])(?:path|file)(?:$|[_-])", re.IGNORECASE)
_PRIVATE_EVENT_KEYS = {
    "cwd",
    "profile",
    "profile_name",
    "session_id",
    "stored_session_id",
    "transport",
    "transport_metadata",
    "url",
}


def _safe_value(
    value: Any, *, key: str = "", depth: int = 0, max_string: int = MAX_STRING
) -> tuple[Any, bool]:
    if _SENSITIVE_KEY.search(key):
        return "[redacted]", True
    if depth >= 4:
        return "[bounded]", True
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, str):
        if _PATH_KEY.search(key) and value.startswith(("/", "~")):
            return "[protected path]", True
        redacted = bool(
            _SENSITIVE_VALUE.search(value)
            or _STANDALONE_CREDENTIAL.search(value)
            or _CREDENTIAL_PATH.search(value)
            or _SIGNED_URL.search(value)
        )
        safe = _SENSITIVE_VALUE.sub("[redacted]", value)
        safe = _STANDALONE_CREDENTIAL.sub("[redacted]", safe)
        if _SIGNED_URL.search(safe):
            safe = "[protected signed URL]"
        if _CREDENTIAL_PATH.search(safe):
            safe = "[protected path]"
        if len(safe) > max_string:
            safe = safe[:max_string] + "…"
            redacted = True
        return safe, redacted
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        changed = len(value) > MAX_ITEMS
        for raw_key, item in list(value.items())[:MAX_ITEMS]:
            safe_key = str(raw_key)[:100]
            if safe_key.lower() in _PRIVATE_EVENT_KEYS:
                changed = True
                continue
            if _SENSITIVE_KEY.search(safe_key):
                output[f"redacted_field_{len(output) + 1}"] = "[redacted]"
                changed = True
                continue
            output[safe_key], item_changed = _safe_value(item, key=safe_key, depth=depth + 1)
            changed = changed or item_changed
        return output, changed
    if isinstance(value, (list, tuple)):
        output = []
        changed = len(value) > MAX_ITEMS
        for item in list(value)[:MAX_ITEMS]:
            safe, item_changed = _safe_value(item, depth=depth + 1)
            output.append(safe)
            changed = changed or item_changed
        return output, changed
    return str(value)[:MAX_STRING], True


def redact_detail(value: Any) -> dict[str, Any]:
    safe, changed = _safe_value(value)
    detail = safe if isinstance(safe, dict) else {"value": safe}
    if changed:
        detail["redacted"] = True
    return detail


def sanitize_text(value: str) -> str:
    """Return generic-detail-bounded safe text using shared credential rules."""
    safe, _ = _safe_value(value)
    return str(safe)


def sanitize_assistant_text(value: str) -> str:
    """Return credential-safe assistant transcript text with a generous hard bound."""
    safe, _ = _safe_value(value, max_string=MAX_ASSISTANT_TEXT, protect_paths=False)
    return str(safe)


def sanitize_user_text(value: str) -> str:
    """Return credential-safe user text without shrinking the accepted API bound."""
    safe, _ = _safe_value(value, max_string=MAX_USER_TEXT)
    return str(safe)


def sanitize_summary(value: str) -> str:
    """Return credential-safe text bounded for event summaries."""
    safe, _ = _safe_value(value, max_string=MAX_SUMMARY)
    return str(safe)


def safe_error_summary(_: BaseException | str) -> str:
    return "Agent connection unavailable. Retry when the agent is online."
