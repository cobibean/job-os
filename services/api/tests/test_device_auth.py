from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jobos_api.device_auth import DeviceAuthenticator


def bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_device_registry_returns_the_identity_owned_by_each_token():
    authenticator = DeviceAuthenticator(
        {
            "mini-device": "mini-device-token-value",
            "macbook-device": "macbook-device-token-value",
        }
    )

    assert authenticator.authenticate(bearer("mini-device-token-value")).device_id == "mini-device"
    assert (
        authenticator.authenticate(bearer("macbook-device-token-value")).device_id
        == "macbook-device"
    )


def test_device_registry_rejects_unknown_and_duplicate_credentials():
    authenticator = DeviceAuthenticator({"mini-device": "mini-device-token-value"})

    try:
        authenticator.authenticate(bearer("unknown-device-token"))
    except HTTPException as error:
        assert error.status_code == 401
    else:
        raise AssertionError("unknown device token was accepted")

    try:
        DeviceAuthenticator(
            {
                "mini-device": "shared-device-token-value",
                "macbook-device": "shared-device-token-value",
            }
        )
    except ValueError as error:
        assert "unique" in str(error)
    else:
        raise AssertionError("duplicate device tokens were accepted")
