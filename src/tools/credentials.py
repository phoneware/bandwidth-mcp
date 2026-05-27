"""Credential management tools — login (OAuth2) and logout.

setCredentials: Takes a client ID and secret, exchanges them for a Bearer
token, extracts account IDs from JWT claims, and reloads authenticated servers.

clearCredentials: Removes stored credentials and access token so that
authenticated API tools return 401 until the user logs in again.
"""

import os

from oauth import get_oauth_token

_AUTH_KEYS = [
    "BW_CLIENT_ID",
    "BW_CLIENT_SECRET",
    "BW_ACCESS_TOKEN",
    "BW_ACCOUNT_ID",
]


async def set_credentials_flow(
    config: dict,
    client_id: str,
    client_secret: str,
) -> dict:
    """Authenticate via OAuth2 and update the shared config.

    Note: This updates the token in config, but tools created at startup
    already have their httpx client headers set. For full effect, the user
    should add credentials to their MCP config and restart the server.
    This tool is primarily useful for Build registration flows.
    """
    token_data = await get_oauth_token(client_id, client_secret)

    config["BW_CLIENT_ID"] = client_id
    config["BW_CLIENT_SECRET"] = client_secret
    config["BW_ACCESS_TOKEN"] = token_data["access_token"]

    accounts = token_data["accounts"]
    if accounts:
        config["BW_ACCOUNT_ID"] = accounts[0]

    return {
        "status": "credentials_set",
        "client_id": client_id,
        "accounts": accounts,
        "active_account": accounts[0] if accounts else None,
        "message": "Authenticated. For best results, add BW_CLIENT_ID and BW_CLIENT_SECRET to your MCP server config and restart.",
    }


def clear_credentials_flow(config: dict) -> dict:
    """Remove stored credentials and access token from the shared config."""
    removed = [key for key in _AUTH_KEYS if key in config]
    for key in _AUTH_KEYS:
        config.pop(key, None)
    config.pop("_authenticated_servers_loaded", None)

    return {
        "status": "logged_out",
        "cleared": removed,
        "message": "Credentials cleared. Authenticated API tools will return 401 until you call setCredentials again.",
    }


def register_credentials_tools(
    mcp,
    config: dict,
):
    """Register the setCredentials and clearCredentials tools on the MCP server.

    setCredentials accepts secret material as tool arguments and is only
    registered for stdio transport. Under remote transports it is omitted
    entirely; clearCredentials remains available since it only mutates
    in-memory state.
    """

    transport = os.environ.get("BW_MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        @mcp.tool(name="setCredentials")
        async def set_credentials(
            client_id: str,
            client_secret: str,
        ) -> dict:
            """Authenticate with Bandwidth using OAuth2 client credentials.

            Exchanges your client ID and secret for a Bearer token and discovers
            your account ID automatically. Primarily for Build registration flows.

            For normal usage, add BW_CLIENT_ID and BW_CLIENT_SECRET to your MCP
            server configuration so authentication happens at startup.

            Args:
                client_id: Bandwidth API client ID (e.g. CLI-xxxxxxxx-xxxx-...)
                client_secret: Bandwidth API client secret
            """
            return await set_credentials_flow(
                config=config,
                client_id=client_id,
                client_secret=client_secret,
            )

    @mcp.tool(name="clearCredentials")
    async def clear_credentials() -> dict:
        """Log out of Bandwidth by clearing stored credentials.

        Removes the client ID, client secret, access token, and account ID
        from the current session. Authenticated API tools will return 401
        until you call setCredentials again.
        """
        return clear_credentials_flow(config)
