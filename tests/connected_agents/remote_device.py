"""Existing JobOS device-auth and profile-fence fixture helpers."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from pathlib import Path


class RemoteAuthorizationError(PermissionError):
    def __init__(self, code: str = "device_authentication_required") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RemoteDeviceFixture:
    label: str
    device_id: str
    profile_id: str
    token: str
    authorized: bool

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-JobOS-Profile-Id": self.profile_id,
        }


def load_remote_devices(path: Path) -> tuple[RemoteDeviceFixture, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(RemoteDeviceFixture(**item) for item in payload["devices"])


def authorize_remote_device(
    headers: dict[str, str],
    *,
    expected_profile_id: str,
    fixtures: tuple[RemoteDeviceFixture, ...],
) -> RemoteDeviceFixture:
    scheme, _, supplied_token = headers.get("Authorization", "").partition(" ")
    supplied_profile = headers.get("X-JobOS-Profile-Id", "")
    matched: RemoteDeviceFixture | None = None
    for fixture in fixtures:
        token_matches = hmac.compare_digest(supplied_token.encode(), fixture.token.encode())
        if token_matches:
            matched = fixture
    if (
        scheme.casefold() != "bearer"
        or matched is None
        or not matched.authorized
        or not hmac.compare_digest(matched.profile_id.encode(), expected_profile_id.encode())
        or not hmac.compare_digest(supplied_profile.encode(), expected_profile_id.encode())
    ):
        raise RemoteAuthorizationError()
    return matched
