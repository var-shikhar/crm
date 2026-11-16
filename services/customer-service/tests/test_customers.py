from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditOutbox

from .conftest import as_role

JANE = {"first_name": "Jane", "last_name": "Doe", "email": "jane.doe@example.com", "phone": "+1 555 0100"}


def events() -> list[dict]:
    with SessionLocal() as db:
        return [row.payload for row in db.scalars(select(AuditOutbox).order_by(AuditOutbox.id))]


def create(client, role="agent", **overrides) -> dict:
    r = client.post("/api/v1/customers", headers=as_role(role), json={**JANE, **overrides})
    assert r.status_code == 201, r.text
    return r.json()


def test_requires_authentication(client):
    assert client.get("/api/v1/customers").status_code == 401
    assert client.get("/api/v1/customers", headers=as_role("stranger")).status_code == 401


def test_create_get_search(client):
    c = create(client)
    assert c["status"] == "active" and c["created_by"] == "agent-id"
    create(client, email="john@example.com", first_name="John", last_name="Smith")

    assert client.get(f"/api/v1/customers/{c['id']}", headers=as_role("viewer")).json()["email"] == JANE["email"]
    page = client.get("/api/v1/customers", headers=as_role("viewer")).json()
    assert page["total"] == 2 and [x["last_name"] for x in page["items"]] == ["Doe", "Smith"]
    assert client.get("/api/v1/customers?q=jane d", headers=as_role("viewer")).json()["total"] == 1
    assert client.get("/api/v1/customers?status=blocked", headers=as_role("viewer")).json()["total"] == 0


def test_validation_and_duplicates(client):
    create(client)
    assert client.post("/api/v1/customers", headers=as_role("agent"), json={**JANE, "email": "JANE.DOE@example.com"}).status_code == 409
    assert client.post("/api/v1/customers", headers=as_role("agent"), json={**JANE, "email": "not-an-email"}).status_code == 422
    assert client.post("/api/v1/customers", headers=as_role("agent"), json={"first_name": "A"}).status_code == 422
    # Callers cannot set server-controlled fields.
    assert client.post("/api/v1/customers", headers=as_role("agent"),
                       json={**JANE, "email": "a@example.com", "status": "blocked"}).status_code == 422


def test_rbac_on_customer_actions(client):
    assert client.post("/api/v1/customers", headers=as_role("viewer"), json=JANE).status_code == 403
    c = create(client)
    assert client.patch(f"/api/v1/customers/{c['id']}", headers=as_role("viewer"), json={"city": "Pune"}).status_code == 403
    assert client.post(f"/api/v1/customers/{c['id']}/block", headers=as_role("agent"), json={"reason": "fraud"}).status_code == 403
    assert client.post(f"/api/v1/customers/{c['id']}/employment", headers=as_role("agent"),
                       json={"employer_name": "Acme"}).status_code == 403
    assert client.post(f"/api/v1/customers/{c['id']}/contacts", headers=as_role("agent"),
                       json={"full_name": "Bob", "relationship_type": "spouse"}).status_code == 403


def test_update_records_field_level_changes(client):
    c = create(client)
    r = client.patch(f"/api/v1/customers/{c['id']}", headers=as_role("agent"), json={"city": "Pune", "phone": None})
    assert r.status_code == 200 and r.json()["city"] == "Pune" and r.json()["phone"] is None
    assert client.patch(f"/api/v1/customers/{c['id']}", headers=as_role("agent"), json={"email": None}).status_code == 422

    last = events()[-1]
    assert last["action"] == "customer.updated"
    assert last["details"]["changes"] == {"city": {"from": None, "to": "Pune"},
                                          "phone": {"from": "+1 555 0100", "to": None}}
    assert last["actor_email"] == "agent@example.com" and last["resource_id"] == c["id"]
    assert last["occurred_at"] and last["actor_roles"] == ["agent"]


def test_block_makes_record_read_only_until_unblocked(client):
    c = create(client)
    url = f"/api/v1/customers/{c['id']}"
    blocked = client.post(f"{url}/block", headers=as_role("manager"), json={"reason": "Suspected fraud"})
    assert blocked.status_code == 200
    body = blocked.json()
    assert body["status"] == "blocked" and body["block_reason"] == "Suspected fraud" and body["blocked_by"] == "manager-id"

    assert client.post(f"{url}/block", headers=as_role("manager"), json={"reason": "again"}).status_code == 409
    assert client.patch(url, headers=as_role("agent"), json={"city": "Pune"}).status_code == 409
    assert client.post(f"{url}/employment", headers=as_role("manager"), json={"employer_name": "Acme"}).status_code == 409
    assert client.get(url, headers=as_role("viewer")).status_code == 200  # still readable
    assert client.get("/api/v1/customers?status=blocked", headers=as_role("viewer")).json()["total"] == 1

    unblocked = client.post(f"{url}/unblock", headers=as_role("manager"))
    assert unblocked.json()["status"] == "active" and unblocked.json()["block_reason"] is None
    assert client.post(f"{url}/unblock", headers=as_role("manager")).status_code == 409
    assert client.patch(url, headers=as_role("agent"), json={"city": "Pune"}).status_code == 200


def test_employment_records(client):
    c = create(client)
    url = f"/api/v1/customers/{c['id']}/employment"
    r = client.post(url, headers=as_role("manager"),
                    json={"employer_name": "Acme Corp", "job_title": "Engineer", "start_date": "2020-01-15",
                          "annual_income": "85000.00", "currency": "USD"})
    assert r.status_code == 201, r.text
    emp = r.json()
    assert client.post(url, headers=as_role("manager"),
                       json={"employer_name": "X", "start_date": "2022-01-01", "end_date": "2021-01-01"}).status_code == 422

    r = client.patch(f"{url}/{emp['id']}", headers=as_role("manager"), json={"is_current": False, "end_date": "2024-06-30"})
    assert r.status_code == 200 and r.json()["is_current"] is False
    assert len(client.get(url, headers=as_role("viewer")).json()) == 1

    other = create(client, email="other@example.com")
    assert client.patch(f"/api/v1/customers/{other['id']}/employment/{emp['id']}", headers=as_role("manager"),
                        json={"job_title": "x"}).status_code == 404  # record belongs to another customer
    assert client.delete(f"{url}/{emp['id']}", headers=as_role("manager")).status_code == 204
    assert client.get(url, headers=as_role("viewer")).json() == []


def test_related_contacts(client):
    c = create(client)
    url = f"/api/v1/customers/{c['id']}/contacts"
    r = client.post(url, headers=as_role("manager"),
                    json={"full_name": "Bob Doe", "relationship_type": "spouse", "phone": "555-0101",
                          "is_emergency_contact": True})
    assert r.status_code == 201, r.text
    contact = r.json()
    r = client.patch(f"{url}/{contact['id']}", headers=as_role("manager"), json={"email": "bob@example.com"})
    assert r.json()["email"] == "bob@example.com"
    assert client.delete(f"{url}/{contact['id']}", headers=as_role("manager")).status_code == 204
    assert client.get(f"/api/v1/customers/{c['id']}/contacts/", headers=as_role("viewer"), follow_redirects=True).json() == []


def test_every_action_is_logged_with_actor_and_time(client):
    c = create(client)
    url = f"/api/v1/customers/{c['id']}"
    client.get(url, headers=as_role("viewer"))
    client.get("/api/v1/customers", headers=as_role("viewer"))
    client.post(f"{url}/contacts", headers=as_role("manager"), json={"full_name": "Bob", "relationship_type": "parent"})
    client.post(f"{url}/block", headers=as_role("manager"), json={"reason": "Chargeback dispute"})
    client.post(f"{url}/unblock", headers=as_role("manager"))

    log = events()
    assert [e["action"] for e in log] == [
        "customer.created", "customer.viewed", "customer.searched",
        "customer.contact.added", "customer.blocked", "customer.unblocked",
    ]
    for e in log:
        assert e["actor_id"] and e["actor_email"] and e["occurred_at"] and e["service"] == "customer-service"
    assert log[4]["details"] == {"reason": "Chargeback dispute"} and log[4]["actor_id"] == "manager-id"

    # A rejected request leaves no audit trail of a change that did not happen.
    client.patch(url, headers=as_role("viewer"), json={"city": "X"})
    assert len(events()) == len(log)
