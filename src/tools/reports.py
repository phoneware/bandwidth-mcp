"""Usage/billing reports over the Bandwidth Dashboard /reports engine.

Bandwidth's report engine is self-describing and async: list the available
report definitions (billing detail records, number inventory, usage, ...),
create an instance with parameters, poll until Status "Ready", then download the file.
(The older /billingreports endpoint is deprecated in favor of this.)

Flow for an agent:
  1. listReports -> pick a report id and see its required parameters.
  2. createReportInstance(report_id, parameters) -> instance id.
  3. getReportInstance until Status is "Ready".
  4. downloadReportFile -> CSV text (zip archives are unpacked in memory,
     large files truncated).
"""

import io
import zipfile
from xml.etree.ElementTree import Element, SubElement

from mcp.types import ToolAnnotations

from tools.discovery import _resolve_account
from tools.numbers import _dashboard_json, _dashboard_send
from urls import dashboard_api_base

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

_MAX_CHARS = 200_000


def register_reports_tools(mcp, config: dict) -> None:
    """Register report tools on the MCP server."""

    @mcp.tool(name="listReports", annotations=_READ)
    async def list_reports(account_id: str = "") -> dict:
        """List the report definitions available on the account (billing
        detail records, inventory, usage, ...), with their ids and the
        parameters each accepts.

        Args:
            account_id: Optional account (see listAccounts).
        """
        return await _dashboard_json(config, "reports", account_id)

    @mcp.tool(name="getReport", annotations=_READ)
    async def get_report(report_id: str, account_id: str = "") -> dict:
        """Get one report definition, including its Parameters spec (names,
        types, which are required). Read this before createReportInstance.

        Args:
            report_id: The report definition id (from listReports).
            account_id: Optional account (see listAccounts).
        """
        return await _dashboard_json(config, f"reports/{report_id}", account_id)

    @mcp.tool(name="listReportInstances", annotations=_READ)
    async def list_report_instances(report_id: str, account_id: str = "") -> dict:
        """List previously generated instances of one report.

        Args:
            report_id: The report definition id (from listReports).
            account_id: Optional account (see listAccounts).
        """
        return await _dashboard_json(
            config, f"reports/{report_id}/instances", account_id
        )

    @mcp.tool(name="createReportInstance", annotations=_WRITE)
    async def create_report_instance(
        report_id: str,
        parameters: dict,
        output_format: str = "csv",
        account_id: str = "",
    ) -> dict:
        """Generate a report: creates an async instance with the given
        parameters. Poll getReportInstance until Status is "Ready", then download.

        Args:
            report_id: The report definition id (from listReports).
            parameters: Name/value pairs from the report's Parameters spec
                (call getReport first; names can contain spaces, e.g.
                "Snapshot Date", dates are YYYY-MM-DD).
            output_format: One of csv, xlsx, pdf, html, xml (default csv;
                csv works best with downloadReportFile).
            account_id: Optional account (see listAccounts).
        """
        body = Element("Instance")
        SubElement(body, "OutputFormat").text = output_format
        params = SubElement(body, "Parameters")
        for name, value in (parameters or {}).items():
            p = SubElement(params, "Parameter")
            SubElement(p, "Name").text = str(name)
            SubElement(p, "Value").text = str(value)
        return await _dashboard_send(
            config, "POST", f"reports/{report_id}/instances", body, account_id
        )

    @mcp.tool(name="getReportInstance", annotations=_READ)
    async def get_report_instance(
        report_id: str, instance_id: str, account_id: str = ""
    ) -> dict:
        """Check the status of a report instance ("Ready" means the file is
        downloadable).

        Args:
            report_id: The report definition id.
            instance_id: The instance id (from createReportInstance).
            account_id: Optional account (see listAccounts).
        """
        return await _dashboard_json(
            config, f"reports/{report_id}/instances/{instance_id}", account_id
        )

    @mcp.tool(name="downloadReportFile", annotations=_READ)
    async def download_report_file(
        report_id: str, instance_id: str, account_id: str = ""
    ) -> dict:
        """Download a Ready report instance. Zip archives are unpacked in
        memory; text is truncated past 200,000 characters.

        Args:
            report_id: The report definition id.
            instance_id: The Ready instance id.
            account_id: Optional account (see listAccounts).
        """
        import httpx

        token = config.get("BW_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("Not authenticated.")
        account = _resolve_account(config, account_id)
        url = (
            f"{dashboard_api_base()}/accounts/{account}"
            f"/reports/{report_id}/instances/{instance_id}/file"
        )
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Download failed ({resp.status_code}): {resp.text[:1000]}"
                )
        data = resp.content
        if data[:2] == b"PK":  # zip archive
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = zf.namelist()
            first = zf.read(names[0]).decode("utf-8", errors="replace") if names else ""
            truncated = len(first) > _MAX_CHARS
            return {
                "archive": names,
                "file": names[0] if names else None,
                "content": first[:_MAX_CHARS],
                "truncated": truncated,
            }
        text = data.decode("utf-8", errors="replace")
        return {"content": text[:_MAX_CHARS], "truncated": len(text) > _MAX_CHARS}
