from typing import Callable, Optional


async def set_credentials_flow(
    config: dict,
    username: str,
    password: str,
    account_id: str,
    reload_callback: Optional[Callable] = None,
) -> dict:
    """Update the shared config with new credentials and reload authenticated servers."""
    config["BW_USERNAME"] = username
    config["BW_PASSWORD"] = password
    config["BW_ACCOUNT_ID"] = account_id

    if reload_callback:
        await reload_callback()

    return {
        "status": "credentials_set",
        "username": username,
        "account_id": account_id,
        "message": "Credentials set. Authenticated API tools are now available.",
    }


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
        account_id: str,
    ) -> dict:
        """Set Bandwidth API credentials after Express Registration.

        Call this after creating an account via createRegistration + verifyRegistrationCode.
        This enables all authenticated API tools (voice, numbers, messaging, etc.).

        Args:
            username: Bandwidth API username
            password: Bandwidth API password
            account_id: Bandwidth account ID
        """
        return await set_credentials_flow(
            config=config,
            username=username,
            password=password,
            account_id=account_id,
            reload_callback=reload_callback,
        )
