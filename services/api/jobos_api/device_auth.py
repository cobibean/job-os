import hmac
from dataclasses import dataclass

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials


@dataclass(frozen=True)
class DeviceIdentity:
    authenticated: bool = True
    device_id: str = "authenticated-device"


class DeviceAuthenticator:
    """Validates one revocable device credential without exposing it downstream."""

    def __init__(self, expected_token: str, device_id: str = "primary-device") -> None:
        self._expected_token = expected_token
        self._device_id = device_id

    def authenticate(
        self,
        credentials: HTTPAuthorizationCredentials | None,
    ) -> DeviceIdentity:
        supplied = credentials.credentials if credentials else ""
        scheme = credentials.scheme if credentials else ""
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.encode(),
            self._expected_token.encode(),
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Device authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return DeviceIdentity(device_id=self._device_id)
