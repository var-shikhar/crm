"""Client for the central auth service.

Resource services never validate tokens or evaluate permissions themselves.
Every protected endpoint asks the auth service: "is this token valid, and may
its owner do X?". That keeps revocation, deactivation and role changes
effective immediately, everywhere.
"""

import time
from dataclasses import asdict, dataclass, field

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .web import request_meta

_bearer = HTTPBearer(auto_error=False, description="Access token issued by the auth service")


@dataclass
class Principal:
    """The authenticated caller: a human user or a service client."""

    id: str
    type: str  # "user" | "service"
    email: str | None = None
    name: str | None = None
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def as_dict(self) -> dict:
        return asdict(self)


class AuthzClient:
    def __init__(self, auth_url: str, client_id: str, client_secret: str, timeout: float = 5.0):
        self._http = httpx.Client(base_url=auth_url, auth=(client_id, client_secret), timeout=timeout)

    def check(self, token: str, permission: str, context: dict | None = None) -> Principal:
        try:
            resp = self._http.post(
                "/api/v1/authz/check",
                json={"token": token, "permission": permission, "context": context or {}},
            )
        except httpx.HTTPError:
            raise HTTPException(503, "Authorization service unavailable")
        if resp.status_code != 200:
            # Fail closed: if we cannot get a decision, nobody gets in.
            raise HTTPException(503, "Authorization service unavailable")
        data = resp.json()
        if not data.get("active"):
            raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
        if not data.get("allowed"):
            raise HTTPException(403, f"Missing permission: {permission}")
        return Principal(**data["principal"])

    def requires(self, permission: str):
        """FastAPI dependency: authenticate the caller and require `permission`."""

        def dependency(
            request: Request,
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> Principal:
            if creds is None:
                raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
            return self.check(creds.credentials, permission, request_meta(request))

        return dependency


class ServiceTokenProvider:
    """Fetches and caches a client-credentials token for service-to-service calls."""

    def __init__(self, auth_url: str, client_id: str, client_secret: str):
        self._http = httpx.Client(base_url=auth_url, auth=(client_id, client_secret), timeout=5.0)
        self._token: str | None = None
        self._expires_at = 0.0

    def __call__(self) -> str:
        if self._token and time.time() < self._expires_at - 30:
            return self._token
        resp = self._http.post("/oauth/token", data={"grant_type": "client_credentials"})
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = time.time() + body["expires_in"]
        return self._token
