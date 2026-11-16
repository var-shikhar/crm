import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="customer-test-")
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp}/test.db", AUDIT_URL="", CLIENT_SECRET="x")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app, authz  # noqa: E402
from crm_common.authz import Principal  # noqa: E402

ROLE_PERMISSIONS = {
    "viewer": ["customer:read"],
    "agent": ["customer:read", "customer:create", "customer:update"],
    "manager": ["customer:read", "customer:create", "customer:update", "customer:block",
                "customer:employment:manage", "customer:contact:manage"],
}


def as_role(role: str) -> dict:
    """Auth header for a fake token; the stubbed auth service maps it to a role."""
    return {"Authorization": f"Bearer {role}"}


@pytest.fixture
def client(monkeypatch):
    def fake_check(token, permission, context=None):
        # Stands in for the auth service's /api/v1/authz/check decision.
        if token not in ROLE_PERMISSIONS:
            raise HTTPException(401, "Invalid or expired token")
        if permission not in ROLE_PERMISSIONS[token]:
            raise HTTPException(403, f"Missing permission: {permission}")
        return Principal(id=f"{token}-id", type="user", email=f"{token}@example.com", name=token.title(),
                         roles=[token], permissions=ROLE_PERMISSIONS[token])

    monkeypatch.setattr(authz, "check", fake_check)
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c
