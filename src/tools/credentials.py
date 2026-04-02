"""setCredentials tool — OAuth2 client credentials flow.

Takes a client ID and secret, exchanges them for a Bearer token,
extracts account IDs from JWT claims, and reloads authenticated servers.
"""

from typing import Callable, Optional

from oauth import get_oauth_token


async def set_credentials_flow(
    config: dict,
    client_id: str,
    client_secret: str,
    reload_callback: Optional[Callable] = None,
) -> dict:
    """Authenticate via OAuth2 and update the shared config."""
    token_data = await get_oauth_token(client_id, client_secret)

    config["BW_CLIENT_ID"] = client_id
    config["BW_CLIENT_SECRET"] = client_secret
    config["BW_ACCESS_TOKEN"] = token_data["access_token"]

    accounts = token_data["accounts"]
    if accounts:
        config["BW_ACCOUNT_ID"] = accounts[0]

    if reload_callback:
        await reload_callback()

    return {
        "status": "credentials_set",
        "client_id": client_id,
        "accounts": accounts,
        "active_account": accounts[0] if accounts else None,
        "message": "Authenticated. Authenticated API tools are now available.",
    }


def register_credentials_tools(
    mcp,
    config: dict,
    reload_callback: Optional[Callable] = None,
):
    """Register the setCredentials tool on the MCP server."""

    @mcp.tool(name="setCredentials")
    async def set_credentials(
        client_id: str,
        client_secret: str,
    ) -> dict:
        """Authenticate with Bandwidth using OAuth2 client credentials.

        Exchanges your client ID and secret for a Bearer token, discovers
        your account ID automatically, and enables all authenticated API tools.

        Args:
            client_id: Bandwidth API client ID (e.g. CLI-xxxxxxxx-xxxx-...)
            client_secret: Bandwidth API client secret
        """
        return await set_credentials_flow(
            config=config,
            client_id=client_id,
            client_secret=client_secret,
            reload_callback=reload_callback,
        )
