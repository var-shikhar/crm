#!/bin/sh
# Generates .env with random secrets. Refuses to overwrite an existing file.
set -eu
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  echo ".env already exists; leaving it untouched." >&2
  exit 0
fi
rand() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$1"; }
admin_password="$(rand 20)"
sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand 32)|" \
  -e "s|^AUTH_DB_PASSWORD=.*|AUTH_DB_PASSWORD=$(rand 32)|" \
  -e "s|^CUSTOMER_DB_PASSWORD=.*|CUSTOMER_DB_PASSWORD=$(rand 32)|" \
  -e "s|^AUDIT_DB_PASSWORD=.*|AUDIT_DB_PASSWORD=$(rand 32)|" \
  -e "s|^SECRET_KEY=.*|SECRET_KEY=$(rand 48)|" \
  -e "s|^CUSTOMER_SERVICE_CLIENT_SECRET=.*|CUSTOMER_SERVICE_CLIENT_SECRET=$(rand 40)|" \
  -e "s|^AUDIT_SERVICE_CLIENT_SECRET=.*|AUDIT_SERVICE_CLIENT_SECRET=$(rand 40)|" \
  -e "s|^BOOTSTRAP_ADMIN_PASSWORD=.*|BOOTSTRAP_ADMIN_PASSWORD=${admin_password}|" \
  .env.example > .env
chmod 600 .env
echo "Wrote .env"
echo "Administrator login: admin@example.com / ${admin_password}"
