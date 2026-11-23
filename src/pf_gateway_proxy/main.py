"""Scale-to-zero routing facade for the PF backend services."""

from __future__ import annotations

import asyncio
import os
import re
from uuid import UUID, uuid4

import httpx
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

_REQUEST_ID = "X-Request-ID"
_API_KEY = "X-API-Key"
_GATEWAY_API_KEY = "X-Gateway-API-Key"
_MAX_REQUEST_ID_LENGTH = 64
_UUID4 = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_FORWARD_HEADERS = {_REQUEST_ID.lower(), _API_KEY.lower(), "accept", "content-type"}
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        *structlog.get_config()["processors"],
    ]
)
logger = structlog.get_logger(__name__)
app = FastAPI(title="pf-gateway-proxy", version="0.1.0")


def _backend_urls() -> dict[str, str]:
    """Load and validate the backend base URLs."""
    values = {
        "reference": os.environ.get("REFERENCE_URL", "").rstrip("/"),
        "income": os.environ.get("INCOME_URL", "").rstrip("/"),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Missing backend URL configuration: {', '.join(missing)}")
    return values


def _request_id(value: str | None) -> tuple[str | None, str | None]:
    """Validate a supplied UUIDv4 or generate one when it is absent."""
    if value is None:
        return str(uuid4()), None
    if len(value) > _MAX_REQUEST_ID_LENGTH or not _UUID4.fullmatch(value):
        return None, "request_id_malformed"
    parsed = UUID(value)
    if parsed.version != 4:
        return None, "request_id_malformed"
    return str(parsed), None


async def _backend_token(audience: str) -> str:
    """Fetch a Google identity token without storing credentials."""
    return await asyncio.to_thread(
        id_token.fetch_id_token, GoogleAuthRequest(), audience
    )


def _forward_headers(request: Request, request_id: str, token: str) -> dict[str, str]:
    """Select safe inbound headers and add backend identity authentication."""
    headers = {}
    for key, value in request.headers.items():
        normalized = key.lower()
        if (
            normalized in _FORWARD_HEADERS
            and normalized not in {_REQUEST_ID.lower(), _GATEWAY_API_KEY.lower()}
        ):
            headers[key] = value
    headers[_REQUEST_ID] = request_id
    headers["Authorization"] = f"Bearer {token}"
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    """Return safe response headers without forwarding hop-by-hop values."""
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _HOP_BY_HOP
    }


@app.get("/health")
async def health() -> dict[str, str]:
    """Return proxy health without calling a backend."""
    return {"status": "ok", "service": "pf-gateway-proxy"}


@app.api_route(
    "/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def forward(request: Request, service: str, path: str) -> Response:
    """Forward one prefixed request to its selected PF backend."""
    request_id, reason = _request_id(request.headers.get(_REQUEST_ID))
    if reason is not None or request_id is None:
        generated = str(uuid4())
        logger.warning("request_id_rejected", request_id=generated, reason_code=reason)
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid X-Request-ID.", "code": reason},
            headers={_REQUEST_ID: generated},
        )

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    backends = _backend_urls()
    backend = backends.get(service)
    if backend is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Unknown backend service."},
            headers={_REQUEST_ID: request_id},
        )

    target = f"{backend}/{path}" if path else backend
    token = await _backend_token(backend)
    headers = _forward_headers(request, request_id, token)
    body = await request.body()
    params = list(request.query_params.multi_items())
    logger.info("proxy_request_started", service=service, path=f"/{path}")
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            backend_response = await client.request(
                request.method,
                target,
                params=params,
                content=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        logger.exception(
            "proxy_backend_error",
            service=service,
            request_id=request_id,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "Backend unavailable."},
            headers={_REQUEST_ID: request_id},
        )
    finally:
        structlog.contextvars.clear_contextvars()

    response_headers = {
        key: value
        for key, value in _response_headers(backend_response).items()
        if key.lower() != _REQUEST_ID.lower()
    }
    response_headers[_REQUEST_ID] = backend_response.headers.get(
        _REQUEST_ID, request_id
    )
    logger.info(
        "proxy_request_completed",
        service=service,
        request_id=request_id,
        status_code=backend_response.status_code,
    )
    return Response(
        content=backend_response.content,
        status_code=backend_response.status_code,
        headers=response_headers,
        media_type=backend_response.headers.get("content-type"),
    )
