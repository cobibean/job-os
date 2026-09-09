import pytest
from jobos_api.external_mcp_config import external_mcp_token
from jobos_api.settings import Settings


def test_external_token_sources_are_private_and_explicit(tmp_path):
    token = "synthetic-external-token-value"
    assert external_mcp_token({}) is None
    assert external_mcp_token({"JOBOS_EXTERNAL_MCP_TOKEN": token}) == token
    path = tmp_path / "external-token"
    path.write_text(token + "\n")
    path.chmod(0o600)
    environment = {"JOBOS_EXTERNAL_MCP_TOKEN_FILE": str(path)}
    assert external_mcp_token(environment) == token
    with pytest.raises(ValueError, match="only one"):
        external_mcp_token({**environment, "JOBOS_EXTERNAL_MCP_TOKEN": token})
    path.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        external_mcp_token(environment)
    path.chmod(0o600)
    link = tmp_path / "token-link"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        external_mcp_token({"JOBOS_EXTERNAL_MCP_TOKEN_FILE": str(link)})
    path.write_text("x" * 5000)
    with pytest.raises(ValueError):
        external_mcp_token(environment)


def test_external_identity_cannot_share_internal_credential(tmp_path):
    with pytest.raises(ValueError, match="unique"):
        Settings(
            device_token="synthetic-device-token",
            mcp_token="synthetic-internal-token",
            external_mcp_token="synthetic-internal-token",
            state_db_path=tmp_path / "state.db",
        )
