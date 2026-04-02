from typing import Callable, Optional


async def set_credentials_flow(
    config: dict,
    username: str,
    password: str,
    account_id: Optional[str] = None,
    reload_callback: Optional[Callable] = None,
) -> dict:
    """Update the shared config with new credentials and reload authenticated servers."""
    config["BW_USERNAME"] = username
    config["BW_PASSWORD"] = password
    if account_id:
        config["BW_ACCOUNT_ID"] = account_id

    if reload_callback:
        await reload_callback()

    result = {
        "status": "credentials_set",
        "username": username,
        "message": "Credentials set. Authenticated API tools are now available.",
    }
    if account_id:
        result["account_id"] = account_id
    return result


def register_credentials_tools(
    mcp,
    config: dict,
    reload_callback: Optional[Callable] = None,
):
    """Register the setCredentials tool on the MCP server."""

    @mcp.tool(name="setCredentials")
    async def set_credentials(
        username: str,
        password: str,
        account_id: Optional[str] = None,
    ) -> dict:
        """Set Bandwidth API credentials to enable authenticated tools.

        Call this with your API username and password. Account ID is optional —
        provide it if you have it, or discover it via the API after authenticating.

        Args:
            username: Bandwidth API username (or client ID)
            password: Bandwidth API password (or client secret)
            account_id: Bandwidth account ID (optional)
        """
        return await set_credentials_flow(
            config=config,
            username=username,
            password=password,
            account_id=account_id,
            reload_callback=reload_callback,
        )
