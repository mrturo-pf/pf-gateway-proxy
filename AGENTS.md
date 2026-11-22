# AGENTS.md — pf-gateway-proxy

Scale-to-zero FastAPI routing facade for the PF API Gateway. This service owns only
routing and transport concerns; business logic remains in `pf-svc-reference` and
`pf-svc-income`.

## Contract

- Routes are `/reference/{path}` and `/income/{path}`.
- Strip exactly one service prefix before forwarding.
- Preserve query strings, request bodies, `X-API-Key`, and `X-Request-ID`.
- Generate a UUIDv4 when `X-Request-ID` is absent; reject malformed values with `400`.
- Forward a Google identity token to each Cloud Run backend.
- Never log API keys, tokens, request bodies, or sensitive response payloads.
- Keep `min-instances=0`; do not add an always-on dependency.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check src tests
uv run uvicorn pf_gateway_proxy.main:app --reload --port 8080
```

## Deployment

Deploy only through the approved Cloud Run workflow after the gateway OpenAPI route
map, IAM bindings, timeout behavior, and rollback evidence are reviewed. The proxy must
run in `us-central1` and use a dedicated runtime service account.
