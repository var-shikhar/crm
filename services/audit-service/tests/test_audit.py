import uuid

from .conftest import as_caller


def event(action="customer.created", **over) -> dict:
    return {
        "event_id": str(uuid.uuid4()), "occurred_at": "2026-11-14T10:00:00+00:00", "service": "customer-service",
        "action": action, "outcome": "success", "actor_id": "u-9", "actor_type": "user",
        "actor_email": "Jane.Agent@example.com", "actor_name": "Jane Agent", "actor_roles": ["agent"],
        "resource_type": "customer", "resource_id": "c-1", "ip_address": "10.0.0.5", "user_agent": "pytest",
        "request_id": "req-1", "details": {"k": "v"}, **over,
    }


def ingest(client, events, caller="svc"):
    return client.post("/api/v1/events/batch", headers=as_caller(caller), json={"events": events})


def test_ingest_is_idempotent(client):
    batch = [event(), event("customer.blocked")]
    assert ingest(client, batch).json() == {"accepted": 2, "duplicates": 0}
    assert ingest(client, batch + [event("customer.viewed")]).json() == {"accepted": 1, "duplicates": 2}


def test_only_service_clients_can_write_and_reading_needs_a_user(client):
    assert ingest(client, [event()], caller="auditor").status_code == 403
    assert ingest(client, [event()], caller="agent").status_code == 403
    assert client.post("/api/v1/events/batch", json={"events": [event()]}).status_code == 401
    assert client.get("/api/v1/events").status_code == 401
    assert client.get("/api/v1/events", headers=as_caller("svc")).status_code == 403


def test_without_audit_read_a_user_sees_only_their_own_account(client):
    mine = event("customer.created", actor_id="u-2", actor_email="agent@example.com")
    about_me = event("user.password.reset", service="auth-service", actor_id="u-1",
                     actor_email="auditor@example.com", resource_type="user", resource_id="u-2")
    my_failed_login = event("auth.login.failed", service="auth-service", outcome="failure", actor_id=None,
                            actor_type="anonymous", actor_email="agent@example.com",
                            resource_type="user", resource_id="u-2")
    someone_else = event("customer.blocked")  # actor u-9, customer c-1
    ingest(client, [mine, about_me, my_failed_login, someone_else])
    agent, auditor = as_caller("agent"), as_caller("auditor")

    page = client.get("/api/v1/events", headers=agent).json()
    assert {e["action"] for e in page["items"]} == {"customer.created", "user.password.reset", "auth.login.failed"}
    assert page["total"] == 3
    # Filters cannot widen the view: asking for another person's events returns nothing.
    assert client.get("/api/v1/events?actor_id=u-9", headers=agent).json()["total"] == 0
    assert client.get("/api/v1/events?actor_email=jane", headers=agent).json()["total"] == 0
    assert client.get("/api/v1/events?resource_id=c-1", headers=agent).json()["total"] == 1  # only their own action

    assert client.get(f"/api/v1/events/{mine['event_id']}", headers=agent).status_code == 200
    assert client.get(f"/api/v1/events/{someone_else['event_id']}", headers=agent).status_code == 404

    export = client.get("/api/v1/events/export?action=customer.*", headers=agent)
    assert export.status_code == 200
    assert "customer.created" in export.text and "customer.blocked" not in export.text

    # The holder of audit:read still sees everything, including the agent's own reads, marked "own".
    everything = client.get("/api/v1/events?page_size=200", headers=auditor).json()
    assert {"customer.blocked", "customer.created"} <= {e["action"] for e in everything["items"]}
    views = [e for e in everything["items"] if e["action"] == "audit.viewed"]
    assert views and all(e["details"]["scope"] == "own" and e["actor_id"] == "u-2" for e in views)


def test_query_filters_and_ordering(client):
    ingest(client, [
        event("customer.created", occurred_at="2026-11-14T10:00:00+00:00"),
        event("customer.blocked", occurred_at="2026-11-14T11:00:00+00:00", resource_id="c-2"),
        event("auth.login.failed", occurred_at="2026-11-14T12:00:00+00:00", service="auth-service",
              outcome="failure", actor_id=None, actor_type="anonymous", actor_email="mallory@example.com"),
    ])
    get = lambda qs: client.get(f"/api/v1/events?{qs}", headers=as_caller("auditor")).json()  # noqa: E731

    page = get("service=customer-service")
    assert page["total"] == 2 and [e["action"] for e in page["items"]] == ["customer.blocked", "customer.created"]
    stored = page["items"][1]
    assert stored["actor_email"] == "Jane.Agent@example.com" and stored["details"] == {"k": "v"}
    assert stored["submitted_by"] == "customer-service" and stored["received_at"]

    assert get("action=customer.*&service=customer-service")["total"] == 2
    assert get("action=customer.blocked")["total"] == 1
    assert get("actor_email=jane.agent")["total"] == 2
    assert get("outcome=failure")["items"][0]["actor_email"] == "mallory@example.com"
    assert get("resource_id=c-2")["total"] == 1
    assert get("from=2026-11-14T10:30:00Z&to=2026-11-14T11:30:00Z")["total"] == 1
    assert len(get("service=customer-service&page_size=1&page=2")["items"]) == 1

    one = client.get(f"/api/v1/events/{stored['event_id']}", headers=as_caller("auditor"))
    assert one.status_code == 200 and one.json()["action"] == "customer.created"
    assert client.get(f"/api/v1/events/{uuid.uuid4()}", headers=as_caller("auditor")).status_code == 404


def test_viewing_the_log_is_itself_logged(client):
    client.get("/api/v1/events?action=customer.*", headers=as_caller("auditor"))
    views = client.get("/api/v1/events?action=audit.viewed", headers=as_caller("auditor")).json()
    assert views["total"] == 1
    assert views["items"][0]["actor_email"] == "auditor@example.com"
    assert views["items"][0]["details"]["filters"] == {"action": "customer.*"}


def test_log_is_append_only(client):
    e = event()
    ingest(client, [e])
    assert client.delete(f"/api/v1/events/{e['event_id']}", headers=as_caller("auditor")).status_code == 405
    assert client.patch(f"/api/v1/events/{e['event_id']}", headers=as_caller("auditor"), json={}).status_code == 405


def test_csv_export(client):
    import csv
    import io

    ingest(client, [
        event("customer.created", occurred_at="2026-11-14T10:00:00+00:00"),
        event("customer.blocked", occurred_at="2026-11-14T11:00:00+00:00", details={"reason": "fraud, suspected"}),
        event("auth.login.failed", service="auth-service", outcome="failure", actor_name="=HYPERLINK(\"http://x\")"),
    ])
    r = client.get("/api/v1/events/export?action=customer.*", headers=as_caller("auditor"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.headers["x-truncated"] == "false"
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [row["action"] for row in rows] == ["customer.blocked", "customer.created"]  # filtered, newest first
    assert rows[0]["actor_email"] == "Jane.Agent@example.com" and rows[0]["actor_roles"] == "agent"
    assert rows[0]["occurred_at"].startswith("2026-11-14T11:00:00")
    assert rows[0]["details"] == '{"reason": "fraud, suspected"}'

    # A value that looks like a spreadsheet formula is neutralised.
    everything = list(csv.DictReader(io.StringIO(client.get("/api/v1/events/export", headers=as_caller("auditor")).text)))
    assert any(row["actor_name"].startswith("'=HYPERLINK") for row in everything)

    # Exporting is recorded with the filters that were used.
    exported = client.get("/api/v1/events?action=audit.exported", headers=as_caller("auditor")).json()
    assert exported["total"] == 2
    assert {"action": "customer.*"} in [e["details"]["filters"] for e in exported["items"]]
