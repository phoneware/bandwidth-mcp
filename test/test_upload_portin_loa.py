import asyncio
import base64
import hashlib
from types import SimpleNamespace

import pytest

from scripts import upload_portin_loa as helper


class FakeClient:
    def __init__(self, *, error=None, result=None):
        self.calls = []
        self.error = error
        self.result = result or SimpleNamespace(is_error=False, data={"id": "file-1"})

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return self.result


def call(client, path, **kwargs):
    return asyncio.run(
        helper.upload_port_in_document(
            client=client,
            order_id="order-1",
            file_path=path,
            confirm=kwargs.pop("confirm", helper.CONFIRMATION),
            **kwargs,
        )
    )


def test_upload_preserves_binary_and_supplies_digest(tmp_path, capsys):
    payload = b"\x00\xfflocal document\n"
    path = tmp_path / "signed.pdf"
    path.write_bytes(payload)
    client = FakeClient()
    assert call(client, path, account_id="account-1", document_type="LOA") == {"id": "file-1"}
    name, arguments = client.calls[0]
    assert name == "uploadPortInLoa"
    assert base64.b64decode(arguments["file_base64"], validate=True) == payload
    assert arguments["file_sha256"] == hashlib.sha256(payload).hexdigest()
    assert arguments["filename"] == "signed.pdf"
    assert arguments["account_id"] == "account-1"
    assert arguments["confirm"] == "UPLOADPORTINLOA"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("kind", ["missing", "empty", "oversize", "directory"])
def test_invalid_file_never_calls_tool(tmp_path, kind):
    path = tmp_path / "signed.pdf"
    if kind == "empty":
        path.write_bytes(b"")
    elif kind == "oversize":
        with path.open("wb") as file:
            file.truncate(helper.MAX_DOCUMENT_BYTES + 1)
    elif kind == "directory":
        path.mkdir()
    client = FakeClient()
    with pytest.raises(helper.UploadError):
        call(client, path)
    assert client.calls == []


def test_maximum_size_is_accepted(tmp_path):
    path = tmp_path / "signed.pdf"
    path.write_bytes(b"x" * helper.MAX_DOCUMENT_BYTES)
    client = FakeClient()
    call(client, path)
    assert len(base64.b64decode(client.calls[0][1]["file_base64"])) == helper.MAX_DOCUMENT_BYTES


def test_confirmation_refusal_precedes_file_access(tmp_path):
    client = FakeClient()
    with pytest.raises(helper.UploadError, match="UPLOADPORTINLOA"):
        call(client, tmp_path / "missing.pdf", confirm="wrong")
    assert client.calls == []


def test_remote_exception_is_not_disclosed_and_retry_is_not_automatic(tmp_path, capsys):
    path = tmp_path / "signed.pdf"
    path.write_bytes(b"secret document")
    client = FakeClient(error=RuntimeError("Bearer secret-token c2VjcmV0"))
    with pytest.raises(helper.UploadError, match="outcome unknown") as error:
        call(client, path)
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert len(client.calls) == 1
    assert capsys.readouterr().out == ""


def test_tool_error_or_unrecognized_result_does_not_claim_success(tmp_path):
    path = tmp_path / "signed.pdf"
    path.write_bytes(b"contents")
    for result in (SimpleNamespace(is_error=True, data={}), SimpleNamespace(is_error=False, data=None)):
        with pytest.raises(helper.UploadError, match="document list"):
            call(FakeClient(result=result), path)


def test_oauth_redirect_is_manual(capsys, monkeypatch):
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *args, **kwargs: pytest.fail("browser opened"))
    oauth = helper.ManualOAuth()
    asyncio.run(oauth.redirect_handler("https://example.test/authorize?state=example"))
    assert "https://example.test/authorize?state=example" in capsys.readouterr().err


def test_cli_suppresses_untrusted_connection_error(monkeypatch, capsys):
    async def fail(_args):
        raise RuntimeError("Bearer secret-token c2VjcmV0")

    monkeypatch.setattr(helper, "_run", fail)
    monkeypatch.setattr("sys.argv", ["upload", "--order-id", "order-1", "--file", "document.pdf", "--confirm", "UPLOADPORTINLOA"])
    assert helper.main() == 1
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err
    assert "document list" in output.err
