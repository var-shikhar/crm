#!/bin/bash
# Runs once, on first start of an empty data directory.
# One database and one login role per service: no service can read another's tables.
set -euo pipefail

create() {
  local user=$1 password=$2 database=$3
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
       -v user="$user" -v password="$password" -v database="$database" <<-'SQL'
	CREATE ROLE :"user" LOGIN PASSWORD :'password';
	CREATE DATABASE :"database" OWNER :"user";
	REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
SQL
}

create auth_svc     "$AUTH_DB_PASSWORD"     auth_db
create customer_svc "$CUSTOMER_DB_PASSWORD" customer_db
create audit_svc    "$AUDIT_DB_PASSWORD"    audit_db
