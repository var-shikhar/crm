import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                out[key] = value
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Our own middleware emits one structured line per request.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


def client_ip(request: Request) -> str | None:
    """Caller IP. The gateway sets X-Real-IP; services are not reachable directly."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else None)


def request_meta(request: Request | None) -> dict:
    if request is None:
        return {}
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": client_ip(request),
        "user_agent": (request.headers.get("user-agent") or "")[:300] or None,
        "method": request.method,
        "path": request.url.path,
    }


def install_common(app: FastAPI, service: str) -> None:
    """Request-id propagation, structured access log, liveness probe."""
    log = logging.getLogger(f"{service}.access")

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path not in ("/healthz", "/readyz"):
            log.info(
                "request",
                extra={
                    "service": service,
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "ip_address": client_ip(request),
                },
            )
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "service": service}
