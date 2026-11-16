import os
import re
import tempfile

_tmp = tempfile.mkdtemp(prefix="auth-test-")
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmp}/test.db",
    JWT_PRIVATE_KEY_PATH=f"{_tmp}/jwt.pem",
    EMAIL_BACKEND="memory",
    AUDIT_URL="",
    BOOTSTRAP_ADMIN_EMAIL="admin@example.com",
    BOOTSTRAP_ADMIN_PASSWORD="Admin-Passw0rd!",
    CUSTOMER_SERVICE_CLIENT_SECRET="customer-secret",
    AUDIT_SERVICE_CLIENT_SECRET="audit-secret",
    MAX_FAILED_LOGINS="3",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import emailer  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import MFA_GRANT  # noqa: E402

ADMIN = ("admin@example.com", "Admin-Passw0rd!")
SERVICE_AUTH = ("customer-service", "customer-secret")


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    emailer.outbox.clear()
    with TestClient(app) as c:
        yield c


def last_email_code() -> str:
    return re.search(r"\b(\d{6})\b", emailer.outbox[-1]["body"]).group(1)


def password_step(client, email, password):
    return client.post("/oauth/token", data={"grant_type": "password", "client_id": "crm-web",
                                             "username": email, "password": password})


def mfa_step(client, mfa_token, otp):
    return client.post("/oauth/token", data={"grant_type": MFA_GRANT, "client_id": "crm-web",
                                             "mfa_token": mfa_token, "otp": otp})


def login(client, email=ADMIN[0], password=ADMIN[1]) -> dict:
    """Full email-MFA login; returns the token response."""
    step1 = password_step(client, email, password)
    assert step1.status_code == 403 and step1.json()["error"] == "mfa_required", step1.text
    step2 = mfa_step(client, step1.json()["mfa_token"], last_email_code())
    assert step2.status_code == 200, step2.text
    return step2.json()


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def make_user(client, admin_tokens, email, roles, password="User-Passw0rd!"):
    r = client.post("/api/v1/users", headers=bearer(admin_tokens),
                    json={"email": email, "full_name": "Test User", "password": password, "roles": roles})
    assert r.status_code == 201, r.text
    return r.json()
