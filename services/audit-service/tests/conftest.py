import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="audit-test-")
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp}/test.db", CLIENT_SECRET="x")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app, authz  # noqa: E402
from crm_common.authz import Principal  # noqa: E402

CALLERS = {
    "svc": Principal(id="customer-service", type="service", name="Customer Service", permissions=["audit:write"]),
    "auditor": Principal(id="u-1", type="user", email="auditor@example.com", name="Auditor", roles=["admin"],
                         permissions=["audit:read"]),
    "agent": Principal(id="u-2", type="user", email="agent@example.com", roles=["agent"], permissions=[]),
}


def as_caller(name: str) -> dict:
    return {"Authorization": f"Bearer {name}"}


@pytest.fixture
def client(monkeypatch):
    def fake_check(token, permission, context=None):
        caller = CALLERS.get(token)
        if caller is None:
            raise HTTPException(401, "Invalid or expired token")
        if permission is not None and not caller.has(permission):
            raise HTTPException(403, f"Missing permission: {permission}")
        return caller

    monkeypatch.setattr(authz, "check", fake_check)
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c
