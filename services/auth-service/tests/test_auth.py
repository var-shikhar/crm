import time

import pyotp
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditOutbox

from .conftest import ADMIN, SERVICE_AUTH, bearer, last_email_code, login, make_user, mfa_step, password_step


def audit_actions() -> list[str]:
    with SessionLocal() as db:
        return [row.payload["action"] for row in db.scalars(select(AuditOutbox).order_by(AuditOutbox.id))]


def authz_check(client, token, permission):
    r = client.post("/api/v1/authz/check", auth=SERVICE_AUTH, json={"token": token, "permission": permission})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------ login and MFA


def test_password_alone_never_yields_tokens(client):
    r = password_step(client, *ADMIN)
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == "mfa_required" and body["mfa_method"] == "email"
    assert "access_token" not in body


def test_email_mfa_login_and_profile(client):
    tokens = login(client)
    me = client.get("/api/v1/me", headers=bearer(tokens)).json()
    assert me["email"] == ADMIN[0]
    assert me["roles"] == ["admin"]
    assert "customer:block" in me["permissions"] and "user:manage" in me["permissions"]
    assert audit_actions() == ["auth.mfa.challenged", "auth.login.succeeded"]


def test_wrong_code_rejected_and_challenge_single_use(client):
    mfa_token = password_step(client, *ADMIN).json()["mfa_token"]
    code = last_email_code()
    wrong = "000000" if code != "000000" else "111111"
    assert mfa_step(client, mfa_token, wrong).json()["error"] == "invalid_grant"
    assert mfa_step(client, mfa_token, code).status_code == 200
    assert mfa_step(client, mfa_token, code).status_code == 400  # already consumed


def test_mfa_attempts_are_capped(client):
    mfa_token = password_step(client, *ADMIN).json()["mfa_token"]
    code = last_email_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert mfa_step(client, mfa_token, wrong).status_code == 400
    assert mfa_step(client, mfa_token, code).status_code == 400  # correct code, but challenge is burnt


def test_bad_password_and_lockout(client):
    for _ in range(3):  # MAX_FAILED_LOGINS=3 in tests
        r = password_step(client, ADMIN[0], "wrong-password")
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    locked = password_step(client, *ADMIN)  # correct password, but locked
    assert locked.status_code == 400 and "locked" in locked.json()["error_description"]
    actions = audit_actions()
    assert actions.count("auth.login.failed") == 4 and "auth.account.locked" in actions


def test_unknown_user_looks_like_bad_password(client):
    r = password_step(client, "nobody@example.com", "whatever-password")
    assert r.status_code == 400 and r.json()["error_description"] == "Invalid email or password"


def test_totp_enrolment_and_login(client):
    tokens = login(client)
    setup = client.post("/api/v1/me/mfa/totp/setup", headers=bearer(tokens)).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and "<svg" in setup["qr_svg"]
    totp = pyotp.TOTP(setup["secret"])

    assert client.post("/api/v1/me/mfa/totp/activate", headers=bearer(tokens), json={"otp": "000000"}).status_code == 400
    activated = client.post("/api/v1/me/mfa/totp/activate", headers=bearer(tokens), json={"otp": totp.now()})
    assert activated.status_code == 200 and activated.json()["mfa_method"] == "totp"

    step1 = password_step(client, *ADMIN).json()
    assert step1["mfa_method"] == "totp"
    # The code used for enrolment cannot be replayed for login.
    assert mfa_step(client, step1["mfa_token"], totp.now()).status_code == 400
    next_code = totp.at(time.time() + 30)  # next time-step, within allowed drift
    assert mfa_step(client, step1["mfa_token"], next_code).status_code == 200

    # Going back to email requires the password.
    assert client.post("/api/v1/me/mfa/email", headers=bearer(tokens), json={"password": "nope"}).status_code == 400
    back = client.post("/api/v1/me/mfa/email", headers=bearer(tokens), json={"password": ADMIN[1]})
    assert back.json()["mfa_method"] == "email"


# ------------------------------------------------------------------ tokens


def test_refresh_rotation_and_reuse_detection(client):
    first = login(client)
    second = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "crm-web",
                                               "refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    second = second.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Replaying the rotated token is treated as theft: the whole session dies.
    replay = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "crm-web",
                                               "refresh_token": first["refresh_token"]})
    assert replay.status_code == 400
    after = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "crm-web",
                                              "refresh_token": second["refresh_token"]})
    assert after.status_code == 400
    assert "auth.refresh.reuse_detected" in audit_actions()


def test_logout_revokes_refresh_and_access_token(client):
    tokens = login(client)
    for tok in (tokens["refresh_token"], tokens["access_token"]):
        assert client.post("/oauth/revoke", data={"token": tok, "client_id": "crm-web"}).status_code == 200
    assert client.get("/api/v1/me", headers=bearer(tokens)).status_code == 401
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "crm-web",
                                          "refresh_token": tokens["refresh_token"]})
    assert r.status_code == 400


def test_client_credentials_and_client_auth(client):
    ok = client.post("/oauth/token", data={"grant_type": "client_credentials"}, auth=SERVICE_AUTH)
    assert ok.status_code == 200 and "audit:write" in ok.json()["scope"] and "refresh_token" not in ok.json()
    bad = client.post("/oauth/token", data={"grant_type": "client_credentials"}, auth=("customer-service", "wrong"))
    assert bad.status_code == 401 and bad.json()["error"] == "invalid_client"
    # The public web client may not use client_credentials, and services may not use password.
    assert client.post("/oauth/token", data={"grant_type": "client_credentials", "client_id": "crm-web"}).status_code == 400
    r = client.post("/oauth/token", data={"grant_type": "password", "username": ADMIN[0], "password": ADMIN[1]},
                    auth=SERVICE_AUTH)
    assert r.json()["error"] == "unauthorized_client"


def test_jwks_and_metadata(client):
    jwks = client.get("/.well-known/jwks.json").json()
    assert jwks["keys"][0]["alg"] == "RS256"
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["token_endpoint"].endswith("/oauth/token")


def test_introspection(client):
    tokens = login(client)
    r = client.post("/oauth/introspect", data={"token": tokens["access_token"]}, auth=SERVICE_AUTH).json()
    assert r["active"] and r["username"] == ADMIN[0] and "customer:read" in r["scope"]
    assert client.post("/oauth/introspect", data={"token": "garbage"}, auth=SERVICE_AUTH).json() == {"active": False}
    assert client.post("/oauth/introspect", data={"token": tokens["access_token"]}).status_code == 401


# ------------------------------------------------------ central authz + RBAC


def test_authz_check_decisions(client):
    admin = login(client)
    make_user(client, admin, "viewer@example.com", ["viewer"])
    viewer = login(client, "viewer@example.com", "User-Passw0rd!")

    allowed = authz_check(client, viewer["access_token"], "customer:read")
    assert allowed["active"] and allowed["allowed"] and allowed["principal"]["roles"] == ["viewer"]

    denied = authz_check(client, viewer["access_token"], "customer:block")
    assert denied["active"] and not denied["allowed"]
    assert "authz.denied" in audit_actions()

    assert authz_check(client, "not-a-token", "customer:read") == {"active": False, "allowed": False, "principal": None}

    # Only authenticated service clients may ask.
    r = client.post("/api/v1/authz/check", json={"token": viewer["access_token"], "permission": "customer:read"})
    assert r.status_code == 401


def test_role_change_and_deactivation_apply_immediately(client):
    admin = login(client)
    user = make_user(client, admin, "agent@example.com", ["agent"])
    agent = login(client, "agent@example.com", "User-Passw0rd!")
    assert not authz_check(client, agent["access_token"], "customer:block")["allowed"]

    # Promote: the *same* token now carries the new permission.
    r = client.patch(f"/api/v1/users/{user['id']}", headers=bearer(admin), json={"roles": ["manager"]})
    assert r.status_code == 200 and r.json()["roles"] == ["manager"]
    assert authz_check(client, agent["access_token"], "customer:block")["allowed"]

    # Deactivate: the token stops working at once, and so does the refresh token.
    client.patch(f"/api/v1/users/{user['id']}", headers=bearer(admin), json={"is_active": False})
    assert not authz_check(client, agent["access_token"], "customer:read")["active"]
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "crm-web",
                                          "refresh_token": agent["refresh_token"]})
    assert r.status_code == 400


def test_user_admin_requires_permission(client):
    admin = login(client)
    make_user(client, admin, "viewer@example.com", ["viewer"])
    viewer = login(client, "viewer@example.com", "User-Passw0rd!")
    assert client.get("/api/v1/users", headers=bearer(viewer)).status_code == 403
    r = client.post("/api/v1/users", headers=bearer(viewer),
                    json={"email": "x@example.com", "full_name": "X", "password": "Another-Passw0rd!"})
    assert r.status_code == 403
    assert client.get("/api/v1/users").status_code == 401
    assert len(client.get("/api/v1/users", headers=bearer(admin)).json()) == 2


def test_user_validation_and_self_protection(client):
    admin = login(client)
    me = client.get("/api/v1/me", headers=bearer(admin)).json()
    assert client.patch(f"/api/v1/users/{me['id']}", headers=bearer(admin), json={"is_active": False}).status_code == 400
    assert client.patch(f"/api/v1/users/{me['id']}", headers=bearer(admin), json={"roles": []}).status_code == 400
    make_user(client, admin, "dup@example.com", [])
    r = client.post("/api/v1/users", headers=bearer(admin),
                    json={"email": "DUP@example.com", "full_name": "D", "password": "Another-Passw0rd!"})
    assert r.status_code == 409
    r = client.post("/api/v1/users", headers=bearer(admin),
                    json={"email": "short@example.com", "full_name": "S", "password": "short"})
    assert r.status_code == 422
    r = client.post("/api/v1/users", headers=bearer(admin),
                    json={"email": "r@example.com", "full_name": "R", "password": "Another-Passw0rd!", "roles": ["nope"]})
    assert r.status_code == 422


def test_custom_role_lifecycle(client):
    admin = login(client)
    r = client.post("/api/v1/roles", headers=bearer(admin),
                    json={"name": "records_officer", "description": "Employment and contacts",
                          "permissions": ["customer:read", "customer:employment:manage", "customer:contact:manage"]})
    assert r.status_code == 201
    role = r.json()
    user = make_user(client, admin, "officer@example.com", ["records_officer"])
    officer = login(client, "officer@example.com", "User-Passw0rd!")
    assert authz_check(client, officer["access_token"], "customer:employment:manage")["allowed"]
    assert not authz_check(client, officer["access_token"], "customer:update")["allowed"]

    # Service-only scopes can never be put on a role.
    r = client.patch(f"/api/v1/roles/{role['id']}", headers=bearer(admin), json={"permissions": ["audit:write"]})
    assert r.status_code == 422
    # In use -> cannot delete; system role -> cannot change.
    assert client.delete(f"/api/v1/roles/{role['id']}", headers=bearer(admin)).status_code == 409
    admin_role = next(x for x in client.get("/api/v1/roles", headers=bearer(admin)).json() if x["name"] == "admin")
    assert client.patch(f"/api/v1/roles/{admin_role['id']}", headers=bearer(admin), json={"permissions": []}).status_code == 400

    client.patch(f"/api/v1/users/{user['id']}", headers=bearer(admin), json={"roles": []})
    assert client.delete(f"/api/v1/roles/{role['id']}", headers=bearer(admin)).status_code == 204


def test_password_change_signs_out_everywhere(client):
    admin = login(client)
    make_user(client, admin, "u@example.com", ["viewer"])
    user = login(client, "u@example.com", "User-Passw0rd!")
    r = client.post("/api/v1/me/password", headers=bearer(user),
                    json={"current_password": "wrong", "new_password": "Brand-New-Passw0rd!"})
    assert r.status_code == 400
    r = client.post("/api/v1/me/password", headers=bearer(user),
                    json={"current_password": "User-Passw0rd!", "new_password": "Brand-New-Passw0rd!"})
    assert r.status_code == 204
    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "crm-web",
                                          "refresh_token": user["refresh_token"]})
    assert r.status_code == 400
    assert password_step(client, "u@example.com", "User-Passw0rd!").status_code == 400
    assert login(client, "u@example.com", "Brand-New-Passw0rd!")["access_token"]


# ------------------------------------------------------- MFA optional per user


def test_mfa_is_required_by_default_and_cannot_be_turned_off(client):
    admin = login(client)
    user = make_user(client, admin, "strict@example.com", ["viewer"])
    assert user["mfa_required"] is True and user["mfa_method"] == "email"
    tokens = login(client, "strict@example.com", "User-Passw0rd!")
    r = client.post("/api/v1/me/mfa/disable", headers=bearer(tokens), json={"password": "User-Passw0rd!"})
    assert r.status_code == 403


def test_admin_can_exempt_a_user_from_mfa(client):
    admin = login(client)
    r = client.post("/api/v1/users", headers=bearer(admin),
                    json={"email": "kiosk@example.com", "full_name": "Kiosk", "password": "User-Passw0rd!",
                          "roles": ["viewer"], "mfa_required": False})
    assert r.status_code == 201 and r.json()["mfa_method"] == "none"
    user = r.json()

    # Password alone now signs this user in, and the log says no second factor was used.
    direct = password_step(client, "kiosk@example.com", "User-Passw0rd!")
    assert direct.status_code == 200 and direct.json()["access_token"] and direct.json()["refresh_token"]
    with SessionLocal() as db:
        last = db.scalars(select(AuditOutbox).order_by(AuditOutbox.id.desc())).first().payload
    assert last["action"] == "auth.login.succeeded" and last["details"]["mfa_method"] == "none"

    # Requiring it again switches the account to email codes and is recorded.
    r = client.patch(f"/api/v1/users/{user['id']}", headers=bearer(admin), json={"mfa_required": True})
    assert r.json()["mfa_required"] is True and r.json()["mfa_method"] == "email"
    assert password_step(client, "kiosk@example.com", "User-Passw0rd!").json()["error"] == "mfa_required"
    with SessionLocal() as db:
        changes = [row.payload for row in db.scalars(select(AuditOutbox)) if row.payload["action"] == "user.updated"]
    assert changes[-1]["details"]["changes"]["mfa_required"] == {"from": False, "to": True}


def test_exempt_user_can_opt_in_and_out(client):
    admin = login(client)
    user = make_user(client, admin, "opt@example.com", ["viewer"])
    client.patch(f"/api/v1/users/{user['id']}", headers=bearer(admin), json={"mfa_required": False})
    # Making MFA optional does not silently remove the factor the user already has.
    tokens = login(client, "opt@example.com", "User-Passw0rd!")
    headers = bearer(tokens)

    assert client.post("/api/v1/me/mfa/disable", headers=headers, json={"password": "wrong"}).status_code == 400
    off = client.post("/api/v1/me/mfa/disable", headers=headers, json={"password": "User-Passw0rd!"})
    assert off.status_code == 200 and off.json()["mfa_method"] == "none"
    assert password_step(client, "opt@example.com", "User-Passw0rd!").status_code == 200

    on = client.post("/api/v1/me/mfa/email", headers=headers, json={"password": "User-Passw0rd!"})
    assert on.json()["mfa_method"] == "email"
    assert password_step(client, "opt@example.com", "User-Passw0rd!").status_code == 403


def test_admin_cannot_exempt_themselves(client):
    admin = login(client)
    me = client.get("/api/v1/me", headers=bearer(admin)).json()
    r = client.patch(f"/api/v1/users/{me['id']}", headers=bearer(admin), json={"mfa_required": False})
    assert r.status_code == 400
