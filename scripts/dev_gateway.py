"""Local stand-in for the nginx gateway (gateway/nginx.conf). Development only.

Serves the web UI and forwards /auth, /crm and /audit to the locally running
services, applying the same rules as the real gateway: internal
service-to-service endpoints are not exposed, and the caller IP is passed on.
"""

import os
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

UPSTREAMS = {
    "auth": os.environ.get("AUTH_UPSTREAM", "http://127.0.0.1:8001"),
    "crm": os.environ.get("CUSTOMER_UPSTREAM", "http://127.0.0.1:8002"),
    "audit": os.environ.get("AUDIT_UPSTREAM", "http://127.0.0.1:8003"),
}
INTERNAL_ONLY = ("/auth/api/v1/authz/", "/auth/oauth/introspect", "/audit/api/v1/events/batch")
WEB_ROOT = (Path(__file__).resolve().parent.parent / "web").resolve()
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "host"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; object-src 'none'; "
                               "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}

http = httpx.AsyncClient(timeout=30.0)


def proxy_to(service: str):
    upstream = UPSTREAMS[service]

    async def proxy(request: Request) -> Response:
        return await forward(request, upstream)

    return proxy


async def forward(request: Request, upstream: str) -> Response:
    if request.url.path.startswith(INTERNAL_ONLY):
        return PlainTextResponse("Not Found", 404)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    headers["host"] = request.headers.get("host", "localhost")
    headers["x-real-ip"] = request.client.host if request.client else ""
    try:
        resp = await http.request(
            request.method,
            f"{upstream}/{request.path_params['path']}",
            params=request.query_params,
            headers=headers,
            content=await request.body(),
        )
    except httpx.HTTPError:
        return PlainTextResponse("Bad Gateway: service is not running", 502)
    out = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP}
    return Response(resp.content, resp.status_code, out)


async def static(request: Request) -> Response:
    target = (WEB_ROOT / request.path_params.get("path", "")).resolve()
    if not target.is_file() or WEB_ROOT not in target.parents:
        target = WEB_ROOT / "index.html"
    return FileResponse(target, headers=SECURITY_HEADERS)


METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE"]
app = Starlette(
    routes=[
        Route("/auth/{path:path}", proxy_to("auth"), methods=METHODS),
        Route("/crm/{path:path}", proxy_to("crm"), methods=METHODS),
        Route("/audit/{path:path}", proxy_to("audit"), methods=METHODS),
        Route("/{path:path}", static),
    ]
)
