from pathlib import Path

import pytest
from jobos_api.settings import DeviceCredential, Settings, parse_device_credentials
from pydantic import ValidationError


def base_settings(**overrides):
    values = {
        "device_token": "mini-device-token-value",
        "mcp_token": "trusted-mcp-token-value",
        "device_id": "mini-device",
        "state_db_path": Path("/tmp/jobos-test.db"),
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_builds_a_secret_safe_device_credential_registry():
    settings = base_settings(
        device_credentials=(
            DeviceCredential(
                device_id="macbook-device",
                token="macbook-device-token-value",
            ),
        )
    )

    assert settings.device_credential_registry() == {
        "mini-device": "mini-device-token-value",
        "macbook-device": "macbook-device-token-value",
    }
    assert "mini-device-token-value" not in repr(settings)
    assert "macbook-device-token-value" not in repr(settings)


@pytest.mark.parametrize(
    "credential",
    [
        DeviceCredential(device_id="mini-device", token="other-device-token-value"),
        DeviceCredential(device_id="macbook-device", token="mini-device-token-value"),
    ],
)
def test_settings_rejects_duplicate_device_ids_or_tokens(credential):
    with pytest.raises(ValidationError, match="unique") as error:
        base_settings(device_credentials=(credential,))
    rendered = str(error.value)
    assert credential.token not in rendered
    assert "mini-device-token-value" not in rendered


def test_device_credential_environment_parser_is_strict_and_secret_safe():
    credentials = parse_device_credentials(
        '{"macbook-device":"macbook-device-token-value"}'
    )
    assert credentials == (
        DeviceCredential(
            device_id="macbook-device",
            token="macbook-device-token-value",
        ),
    )

    secret = "secret-that-must-not-escape"
    with pytest.raises(ValueError, match="invalid") as error:
        parse_device_credentials(f'{{"device":{{"token":"{secret}"}}}}')
    assert secret not in str(error.value)


def test_device_credential_parser_does_not_chain_secret_bearing_validation_errors():
    secret = "secret-that-must-not-escape"
    with pytest.raises(ValueError, match="invalid") as error:
        parse_device_credentials(f'{{"bad id":"{secret}"}}')

    assert error.value.__cause__ is None
    assert secret not in str(error.value)
