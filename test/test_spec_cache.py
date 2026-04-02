import pytest
import yaml
import httpx
from pathlib import Path
from src.server_utils import (
    fetch_openapi_spec,
    _save_spec_cache,
    _load_spec_cache,
    CACHE_DIR,
)


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """Redirect spec cache to a temp directory."""
    monkeypatch.setattr("src.server_utils.CACHE_DIR", tmp_path)
    return tmp_path


SAMPLE_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Test", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {},
}


def test_save_and_load_cache(tmp_cache):
    url = "https://dev.bandwidth.com/spec/test.yml"
    _save_spec_cache(url, SAMPLE_SPEC)
    loaded = _load_spec_cache(url)
    assert loaded == SAMPLE_SPEC


def test_load_cache_returns_none_when_missing(tmp_cache):
    loaded = _load_spec_cache("https://dev.bandwidth.com/spec/missing.yml")
    assert loaded is None


@pytest.mark.asyncio
async def test_fetch_caches_on_success(tmp_cache, httpx_mock):
    url = "https://dev.bandwidth.com/spec/cached-test.yml"
    httpx_mock.add_response(url=url, text=yaml.dump(SAMPLE_SPEC))
    result = await fetch_openapi_spec(url)
    assert result["info"]["title"] == "Test"
    cached = _load_spec_cache(url)
    assert cached is not None
    assert cached["info"]["title"] == "Test"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_cache(tmp_cache, httpx_mock):
    url = "https://dev.bandwidth.com/spec/fallback-test.yml"
    _save_spec_cache(url, SAMPLE_SPEC)
    httpx_mock.add_exception(httpx.ConnectError("Network down"), url=url)
    result = await fetch_openapi_spec(url)
    assert result["info"]["title"] == "Test"


@pytest.mark.asyncio
async def test_fetch_raises_when_no_cache_no_network(tmp_cache, httpx_mock):
    url = "https://dev.bandwidth.com/spec/nowhere.yml"
    httpx_mock.add_exception(httpx.ConnectError("Network down"), url=url)
    with pytest.raises(RuntimeError, match="Failed to fetch"):
        await fetch_openapi_spec(url)
