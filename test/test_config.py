import os
import pytest
from unittest.mock import patch, AsyncMock


def test_load_config_without_credentials():
    """MCP server should start without BW_CLIENT_ID/BW_CLIENT_SECRET."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BW_")}
    with patch.dict(os.environ, env, clear=True):
        from src.config import load_config

        with pytest.warns(UserWarning, match="BW_CLIENT_ID/BW_CLIENT_SECRET not set"):
            config = load_config()
        assert config.get("BW_ACCESS_TOKEN") is None


def test_load_config_with_credentials():
    """When OAuth credentials are provided, they're stored in config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BW_")}
    env["BW_CLIENT_ID"] = "CLI-test"
    env["BW_CLIENT_SECRET"] = "secret"
    with patch.dict(os.environ, env, clear=True):
        from src.config import load_config

        config = load_config()
        assert config["BW_CLIENT_ID"] == "CLI-test"
        assert config["BW_CLIENT_SECRET"] == "secret"


@pytest.mark.asyncio
async def test_authenticate_config_does_oauth():
    """authenticate_config exchanges credentials for a token."""
    from src.config import authenticate_config

    config = {"BW_CLIENT_ID": "CLI-test", "BW_CLIENT_SECRET": "secret"}
    mock_token = {
        "access_token": "test-token",
        "accounts": ["99999"],
        "token_type": "bearer",
    }
    with patch("oauth.get_oauth_token", new_callable=AsyncMock) as mock_oauth:
        mock_oauth.return_value = mock_token
        await authenticate_config(config)

    assert config["BW_ACCESS_TOKEN"] == "test-token"
    assert config["BW_ACCOUNT_ID"] == "99999"


@pytest.mark.asyncio
async def test_authenticate_config_skips_without_credentials():
    """authenticate_config is a no-op without client credentials."""
    from src.config import authenticate_config

    config = {}
    await authenticate_config(config)
    assert "BW_ACCESS_TOKEN" not in config
