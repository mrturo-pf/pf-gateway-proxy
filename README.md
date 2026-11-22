# pf-gateway-proxy

A small scale-to-zero routing facade behind Google Cloud API Gateway.

```text
API Gateway /reference/* ─┐
                          ├─> pf-gateway-proxy ─> pf-svc-reference
API Gateway /income/* ────┘                    └─> pf-svc-income
```

The proxy strips one public prefix, forwards the original backend path, query string,
body, `X-API-Key`, and `X-Request-ID`, and adds a Google identity token for the target
Cloud Run service. It contains no PF business logic. API Gateway uses
`APPEND_PATH_TO_ADDRESS` to send the full prefixed path to this proxy; the proxy removes
`/reference` or `/income`.

## Required environment

```text
REFERENCE_URL=https://pf-svc-reference-....a.run.app
INCOME_URL=https://pf-svc-income-....a.run.app
```

The runtime identity obtains Application Default Credentials in Cloud Run. No service
account key belongs in this repository.

## Production status

The service is deployed in `us-central1` with `min-instances=0`, a maximum of two
instances, 512 MiB memory, one CPU, and a 30-second timeout. API Gateway invokes it
with a dedicated service account; direct public invocation remains disabled.

The route map is generated from the live backend OpenAPI documents and currently covers
37 paths and 46 operations. Mutating routes still require the normal application API
keys and should be tested with synthetic validation fixtures before broad rollout.
