#!/usr/bin/env python3
"""End-to-end check against a locally running stack (./scripts/run-local.sh start).

Standard library only. Exercises the real services through the gateway:
MFA login, user/role admin, RBAC decisions, the customer lifecycle, and the
resulting activity log. Exits non-zero on the first failed expectation.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8080")
AUTH_LOG = ROOT / ".local" / "logs" / "auth.log"
MFA_GRANT = "urn:crm:params:oauth:grant-type:mfa-otp"
ENV = dict(line.split("=", 1) for line in (ROOT / ".local" / "env").read_text().splitlines() if "=" in line)

passed = 0


def check(label: str, condition: bool, detail: object = "") -> None:
    global passed
    if not condition:
        print(f"  FAIL  {label}  {detail}")
        sys.exit(1)
    passed += 1
    print(f"  ok    {label}")


def call(method: str, path: str, *, token: str | None = None, body=None, form: dict | None = None):
    headers, data = {}, None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as err:
        raw, status = err.read(), err.code
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")


def emailed_code(email: str) -> str:
    """The console email backend writes one-time codes to the auth service log."""
    for _ in range(20):
        lines = [ln for ln in AUTH_LOG.read_text().splitlines() if f"to={email}" in ln]
        if lines:
            return re.search(r"sign in is: (\d{6})", lines[-1]).group(1)
        time.sleep(0.25)
    raise RuntimeError(f"no code logged for {email}")


def login(email: str, password: str) -> dict:
    status, step1 = call("POST", "/auth/oauth/token", form={
        "grant_type": "password", "client_id": "crm-web", "username": email, "password": password})
    if status == 200:  # account exempt from MFA
        return step1
    assert status == 403 and step1["error"] == "mfa_required", (status, step1)
    status, tokens = call("POST", "/auth/oauth/token", form={
        "grant_type": MFA_GRANT, "client_id": "crm-web", "mfa_token": step1["mfa_token"], "otp": emailed_code(email)})
    assert status == 200, (status, tokens)
    return tokens


def main() -> None:
    run = str(int(time.time()))
    admin_email, admin_password = ENV["BOOTSTRAP_ADMIN_EMAIL"], ENV["BOOTSTRAP_ADMIN_PASSWORD"]

    print("Authentication")
    status, body = call("POST", "/auth/oauth/token", form={
        "grant_type": "password", "client_id": "crm-web", "username": admin_email, "password": admin_password})
    check("password alone returns mfa_required, not tokens", status == 403 and "access_token" not in body, body)
    status, body = call("POST", "/auth/oauth/token", form={
        "grant_type": "password", "client_id": "crm-web", "username": admin_email, "password": "wrong-password"})
    check("wrong password is rejected", status == 400 and body["error"] == "invalid_grant", body)
    admin = login(admin_email, admin_password)["access_token"]
    status, me = call("GET", "/auth/api/v1/me", token=admin)
    check("admin signs in with email code as second factor", status == 200 and me["roles"] == ["admin"], me)

    print("User and role management")
    password = "Smoke-Test-Passw0rd!"
    users = {}
    for role in ("manager", "agent"):
        email = f"{role}.{run}@example.com"
        status, user = call("POST", "/auth/api/v1/users", token=admin, body={
            "email": email, "full_name": f"Smoke {role.title()}", "password": password, "roles": [role]})
        check(f"admin creates a user with role {role}", status == 201 and user["roles"] == [role], user)
        users[role] = {"email": email, "id": user["id"], "token": login(email, password)["access_token"]}
    agent, manager = users["agent"]["token"], users["manager"]["token"]
    status, _ = call("GET", "/auth/api/v1/users", token=agent)
    check("agent cannot list users (403)", status == 403)

    print("MFA optional per user")
    kiosk = f"kiosk.{run}@example.com"
    status, user = call("POST", "/auth/api/v1/users", token=admin, body={
        "email": kiosk, "full_name": "Smoke Kiosk", "password": password, "roles": ["viewer"], "mfa_required": False})
    check("admin creates a user exempt from MFA", status == 201 and user["mfa_method"] == "none", user)
    status, body = call("POST", "/auth/oauth/token", form={
        "grant_type": "password", "client_id": "crm-web", "username": kiosk, "password": password})
    check("exempt user signs in with password only", status == 200 and "access_token" in body, body)
    status, body = call("PATCH", f"/auth/api/v1/users/{user['id']}", token=admin, body={"mfa_required": True})
    check("admin makes MFA required again", status == 200 and body["mfa_method"] == "email", body)
    status, body = call("POST", "/auth/oauth/token", form={
        "grant_type": "password", "client_id": "crm-web", "username": kiosk, "password": password})
    check("that user is now asked for a code", status == 403 and body["error"] == "mfa_required", body)
    status, body = call("POST", "/auth/api/v1/me/mfa/disable", token=agent, body={"password": password})
    check("a user who must use MFA cannot turn it off (403)", status == 403, body)

    print("Customers and access control")
    status, customer = call("POST", "/crm/api/v1/customers", token=agent, body={
        "first_name": "Priya", "last_name": "Sharma", "email": f"priya.{run}@example.com", "phone": "+91 98765 43210"})
    check("agent adds a customer", status == 201 and customer["status"] == "active", customer)
    cid = customer["id"]
    url = f"/crm/api/v1/customers/{cid}"
    status, body = call("PATCH", url, token=agent, body={"city": "Bengaluru"})
    check("agent edits the customer", status == 200 and body["city"] == "Bengaluru", body)
    status, _ = call("POST", f"{url}/block", token=agent, body={"reason": "not allowed"})
    check("agent cannot block (403)", status == 403)
    status, _ = call("POST", f"{url}/employment", token=agent, body={"employer_name": "Acme"})
    check("agent cannot add employment (403)", status == 403)
    status, _ = call("GET", url)
    check("no token is rejected (401)", status == 401)

    status, emp = call("POST", f"{url}/employment", token=manager, body={
        "employer_name": "Infosys", "job_title": "Analyst", "start_date": "2021-04-01",
        "annual_income": "1800000.00", "currency": "INR"})
    check("manager adds an employment record", status == 201, emp)
    status, contact = call("POST", f"{url}/contacts", token=manager, body={
        "full_name": "Rahul Sharma", "relationship_type": "spouse", "phone": "+91 98765 00000"})
    check("manager adds a related contact", status == 201, contact)
    status, body = call("POST", f"{url}/block", token=manager, body={"reason": "KYC documents expired"})
    check("manager blocks the customer", status == 200 and body["status"] == "blocked", body)
    status, _ = call("PATCH", url, token=agent, body={"city": "Mumbai"})
    check("blocked customer cannot be edited (409)", status == 409)
    status, body = call("POST", f"{url}/unblock", token=manager)
    check("manager unblocks the customer", status == 200 and body["status"] == "active", body)

    print("Central authorization takes effect immediately")
    call("PATCH", f"/auth/api/v1/users/{users['agent']['id']}", token=admin, body={"is_active": False})
    status, _ = call("GET", url, token=agent)
    check("deactivated user's existing token stops working (401)", status == 401)

    print("Gateway")
    status, _ = call("POST", "/auth/api/v1/authz/check", body={"token": admin, "permission": "customer:read"})
    check("internal authz endpoint is not exposed (404)", status == 404)

    print("Activity log")
    status, _ = call("GET", "/audit/api/v1/events", token=agent)
    check("deactivated user cannot read the log (401)", status == 401)
    def events(query: str, done) -> list[dict]:
        """Query the log, waiting briefly: delivery is asynchronous (outbox)."""
        items: list[dict] = []
        for _ in range(20):
            status, page = call("GET", f"/audit/api/v1/events?page_size=100&{query}", token=admin)
            items = page["items"] if status == 200 else []
            if done(items):
                break
            time.sleep(0.5)
        return items

    expected = {"customer.created", "customer.updated", "customer.blocked", "customer.unblocked",
                "customer.employment.added", "customer.contact.added"}
    lifecycle = events(f"action=customer.*&actor_email={run}@example.com",
                       lambda items: expected <= {e["action"] for e in items})
    check("customer lifecycle is in the activity log", expected <= {e["action"] for e in lifecycle},
          sorted({e["action"] for e in lifecycle}))
    blocked = next(e for e in lifecycle if e["action"] == "customer.blocked")
    check("event records who, when, where and what",
          blocked["actor_email"] == users["manager"]["email"] and blocked["actor_roles"] == ["manager"]
          and blocked["resource_id"] == cid and blocked["occurred_at"] and blocked["ip_address"]
          and blocked["details"]["reason"] == "KYC documents expired", blocked)

    wanted = {"customer:block", "customer:employment:manage", "user:read"}
    denied = events(f"action=authz.denied&actor_id={users['agent']['id']}",
                    lambda items: wanted <= {e["details"]["permission"] for e in items})
    check("denied attempts are logged", wanted <= {e["details"]["permission"] for e in denied}, denied)
    failures = events("action=auth.login.*&outcome=failure", lambda items: len(items) >= 1)
    check("failed logins are logged", len(failures) >= 1)
    admin_actions = events(f"action=user.*&resource_id={users['agent']['id']}",
                           lambda items: {"user.created", "user.updated"} <= {e["action"] for e in items})
    check("user administration is logged", {"user.created", "user.updated"} <= {e["action"] for e in admin_actions},
          admin_actions)

    status, text = call("GET", f"/audit/api/v1/events/export?action=customer.*&actor_email={run}@example.com", token=admin)
    lines = text.strip().splitlines() if isinstance(text, str) else []
    check("activity log exports as CSV with the same filters",
          status == 200 and lines and lines[0].startswith("occurred_at,service,action") and len(lines) - 1 == len(lifecycle)
          and any("customer.blocked" in ln and users["manager"]["email"] in ln for ln in lines), (status, lines[:2]))
    status, _ = call("GET", "/audit/api/v1/events/export", token=manager)
    check("manager (audit:read) can export", status == 200)

    print("Sign out")
    call("POST", "/auth/oauth/revoke", form={"token": admin, "client_id": "crm-web"})
    status, _ = call("GET", "/auth/api/v1/me", token=admin)
    check("revoked access token is rejected (401)", status == 401)

    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()
