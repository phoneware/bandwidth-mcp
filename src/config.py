import os
import warnings
from typing import Any, Dict, List, Optional
from argparse import ArgumentParser, Namespace

from profiles import resolve_profile


def load_config() -> Dict[str, Any]:
    """Load Bandwidth configuration from environment variables."""
    config: Dict[str, Any] = {}

    all_vars = [
        "BW_CLIENT_ID",
        "BW_CLIENT_SECRET",
        "BW_ACCOUNT_ID",
        "BW_NUMBER",
        "BW_MESSAGING_APPLICATION_ID",
        "BW_VOICE_APPLICATION_ID",
    ]

    for var in all_vars:
        value = os.environ.get(var)
        if value:
            config[var] = value

    if "BW_CLIENT_ID" not in config or "BW_CLIENT_SECRET" not in config:
        warnings.warn(
            "BW_CLIENT_ID/BW_CLIENT_SECRET not set. Only Express Registration tools will be available. "
            "Use the setCredentials tool to authenticate and enable all API tools.",
            UserWarning,
        )

    # Transport config
    transport_vars = [
        "BW_MCP_TRANSPORT",
        "BW_MCP_HOST",
        "BW_MCP_PORT",
        "BW_MCP_AUTH_TOKEN",
        "BW_MCP_BASE_URL",
        "BW_VOICE_FALLBACK_NUMBER",
    ]
    for var in transport_vars:
        value = os.environ.get(var)
        if value:
            config[var] = value

    return config


async def authenticate_config(config: Dict[str, Any]) -> None:
    """If client credentials are present, do OAuth2 token exchange.

    Mutates config in place — adds BW_ACCESS_TOKEN and BW_ACCOUNT_ID.
    """
    if "BW_CLIENT_ID" not in config or "BW_CLIENT_SECRET" not in config:
        return

    from oauth import get_oauth_token

    try:
        token_data = await get_oauth_token(
            config["BW_CLIENT_ID"], config["BW_CLIENT_SECRET"]
        )
        config["BW_ACCESS_TOKEN"] = token_data["access_token"]
        accounts = token_data["accounts"]
        if accounts and "BW_ACCOUNT_ID" not in config:
            config["BW_ACCOUNT_ID"] = accounts[0]
    except Exception as e:
        warnings.warn(f"OAuth2 token exchange failed at startup: {e}", UserWarning)


def _parse_cli_args(args: Optional[List[str]] = None) -> Namespace:
    """Parse command line arguments with proper type hints."""
    parser = ArgumentParser(description="Bandwidth MCP Server")

    parser.add_argument(
        "--tools",
        help="Comma-separated list of tool names to enable. If not specified, all tools are enabled.",
        type=str,
    )
    parser.add_argument(
        "--exclude-tools",
        help="Comma-separated list of tool names to disable.",
        type=str,
    )
    parser.add_argument(
        "--profile",
        help="Named tool profile (or comma-separated profiles) to enable. Use 'full' for all tools.",
        type=str,
    )
    parser.add_argument(
        "--transport",
        help="Transport type: stdio (default), sse, or streamable-http.",
        type=str,
        choices=["stdio", "sse", "streamable-http"],
    )
    parser.add_argument(
        "--port",
        help="Port for HTTP transport (default: 8080).",
        type=int,
    )

    return parser.parse_known_args(args)[0]


def _parse_arg_list(arg_string: str) -> List[str]:
    """Parse a comma-separated argument string into a list."""
    return [item.strip() for item in arg_string.split(",") if item.strip()]


def _parse_flags(cli_arg: Optional[str], env_var: str) -> Optional[List[str]]:
    """Get flag values from CLI argument or environment variable."""
    if cli_arg:
        return _parse_arg_list(cli_arg)
    env_value = os.getenv(env_var)
    if env_value:
        return _parse_arg_list(env_value)
    return None


def get_profile_tools() -> Optional[List[str]]:
    """Get tool list from profile, if specified."""
    args = _parse_cli_args()
    profile_str = args.profile or os.getenv("BW_MCP_PROFILE")
    return resolve_profile(profile_str)


def get_enabled_tools() -> Optional[List[str]]:
    """Get the list of enabled tools from CLI args, env var, or profile."""
    args = _parse_cli_args()
    explicit = _parse_flags(args.tools, "BW_MCP_TOOLS")
    if explicit:
        return explicit
    return get_profile_tools()


def get_excluded_tools() -> Optional[List[str]]:
    """Get the list of excluded tools from CLI args or environment variable."""
    args = _parse_cli_args()
    return _parse_flags(args.exclude_tools, "BW_MCP_EXCLUDE_TOOLS")


def get_transport_config() -> Dict[str, Any]:
    """Get transport configuration from CLI args and env vars."""
    args = _parse_cli_args()
    return {
        "transport": args.transport or os.getenv("BW_MCP_TRANSPORT", "stdio"),
        "host": os.getenv("BW_MCP_HOST", "0.0.0.0"),
        "port": args.port or int(os.getenv("BW_MCP_PORT", "8080")),
    }
