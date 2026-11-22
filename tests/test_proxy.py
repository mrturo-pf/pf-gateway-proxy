"""Tests for the PF gateway proxy contract."""

import httpx
import pytest

from pf_gateway_proxy import main

_REAL_ASYNC_CLIENT = httpx.AsyncClient
_VALID_ID = "550e8400-e29b-41d4-a716-446655440000"


class _FakeClient:
    """Capture one backend request and return a synthetic response."""

    calls: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        """Accept the production client constructor arguments."""

    async def __aenter__(self):
        """Return the fake async client."""
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """Close the fake async client."""

    async def request(self, method, url, **kwargs):
        """Capture the request and return a backend response."""
        self.calls.append({"method": method, "url": url, **kwargs})
        return httpx.Response(
            200,
            json={"ok": True},
            headers={
                "content-type": "application/json",
                "X-Request-ID": kwargs["headers"]["X-Request-ID"],
            },
        )


@pytest.fixture(autouse=True)
def _configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure synthetic backends and identity tokens."""
    monkeypatch.setenv("REFERENCE_URL", "https://reference.test")
    monkeypatch.setenv("INCOME_URL", "https://income.test")
    monkeypatch.setattr(main, "_backend_token", lambda audience: _token(audience))
    _FakeClient.calls = []
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeClient)


async def _token(audience: str) -> str:
    """Return a deterministic synthetic identity token."""
    return f"token-for:{audience}"


@pytest.mark.asyncio
async def test_proxy_strips_prefix_and_preserves_request_contract() -> None:
    """The proxy forwards the original path, query, API key, and request ID."""
    async with _REAL_ASYNC_CLIENT(
        transport=httpx.ASGITransport(app=main.app), base_url="http://proxy"
    ) as client:
        response = await client.get(
            "/reference/monetary-units?include=active",
            headers={"X-API-Key": "application-key", "X-Request-ID": _VALID_ID},
        )

    call = _FakeClient.calls[0]
    assert response.status_code == 200
    assert call["method"] == "GET"
    assert call["url"] == "https://reference.test/monetary-units"
    assert call["params"] == [("include", "active")]
    assert call["headers"]["x-api-key"] == "application-key"
    assert call["headers"]["X-Request-ID"] == _VALID_ID
    assert call["headers"]["Authorization"] == "Bearer token-for:https://reference.test"
    assert response.headers["X-Request-ID"] == _VALID_ID


@pytest.mark.asyncio
async def test_missing_id_is_generated_and_returned() -> None:
    """An omitted correlation ID is generated for the backend call."""
    async with _REAL_ASYNC_CLIENT(
        transport=httpx.ASGITransport(app=main.app), base_url="http://proxy"
    ) as client:
        response = await client.get("/income/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert _FakeClient.calls[0]["headers"]["X-Request-ID"] == response.headers[
        "X-Request-ID"
    ]


@pytest.mark.asyncio
async def test_invalid_id_is_rejected_without_backend_call() -> None:
    """Malformed IDs receive 400 and never reach a backend."""
    async with _REAL_ASYNC_CLIENT(
        transport=httpx.ASGITransport(app=main.app), base_url="http://proxy"
    ) as client:
        response = await client.get(
            "/reference/health", headers={"X-Request-ID": "bad"}
        )

    assert response.status_code == 400
    assert response.json()["code"] == "request_id_malformed"
    assert _FakeClient.calls == []
