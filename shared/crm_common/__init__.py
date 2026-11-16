"""Code shared by all CRM microservices.

Kept deliberately small: the authorization client, the audit outbox, and
request/logging plumbing. Each service image gets its own copy at build time,
so services stay independently deployable.
"""
