import hmac
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials


@dataclass(frozen=True)
class DeviceIdentity:
    authenticated: bool = True
    device_id: str = "authenticated-device"


class DeviceAuthenticator:
    """Maps revocable device credentials to identities without exposing secrets."""

    def __init__(
        self,
        expected_token: str | Mapping[str, str],
        device_id: str = "primary-device",
    ) -> None:
        credentials = (
            {device_id: expected_token}
            if isinstance(expected_token, str)
            else dict(expected_token)
        )
        if not credentials:
            raise ValueError("at least one device credential is required")
        if len(set(credentials.values())) != len(credentials):
            raise ValueError("device credentials must be unique")
        self._credentials = tuple(credentials.items())

    def authenticate(
        self,
        credentials: HTTPAuthorizationCredentials | None,
    ) -> DeviceIdentity:
        supplied = credentials.credentials if credentials else ""
        scheme = credentials.scheme if credentials else ""
        matched_device = None
        for candidate_device, expected_token in self._credentials:
            if hmac.compare_digest(supplied.encode(), expected_token.encode()):
                matched_device = candidate_device
        if scheme.lower() != "bearer" or matched_device is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Device authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return DeviceIdentity(device_id=matched_device)
