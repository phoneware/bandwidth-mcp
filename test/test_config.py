import os
import pytest
from unittest.mock import patch


def test_load_config_without_credentials():
    """MCP server should start without BW_USERNAME/BW_PASSWORD."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BW_")}
    with patch.dict(os.environ, env, clear=True):
        from src.config import load_config
        with pytest.warns(UserWarning, match="BW_USERNAME/BW_PASSWORD not set"):
            config = load_config()
        assert config.get("BW_USERNAME") is None


def test_load_config_with_credentials():
    """When credentials are provided, they should be in the config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BW_")}
    env["BW_USERNAME"] = "user"
    env["BW_PASSWORD"] = "pass"
    with patch.dict(os.environ, env, clear=True):
        from src.config import load_config
        config = load_config()
        assert config["BW_USERNAME"] == "user"
        assert config["BW_PASSWORD"] == "pass"
