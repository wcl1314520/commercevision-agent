#!/bin/sh
set -eu

: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}"
: "${MYSQL_RUNTIME_PASSWORD:?MYSQL_RUNTIME_PASSWORD is required}"

mysql_admin() {
    MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysql \
        --protocol=TCP \
        --host=mysql \
        --user=root \
        "$@"
}

mysql_runtime() {
    MYSQL_PWD="${MYSQL_RUNTIME_PASSWORD}" mysql \
        --protocol=TCP \
        --host=mysql \
        --user=commercevision \
        --database=commercevision \
        "$@"
}

mysql_admin < /opt/commercevision/reconcile-runtime-grants.sql

runtime_grants="$(mysql_runtime --batch --skip-column-names --execute='SHOW GRANTS FOR CURRENT_USER')"
required_grant='GRANT SELECT, INSERT, UPDATE, DELETE ON `commercevision`.*'
if ! printf '%s\n' "${runtime_grants}" | grep --fixed-strings --quiet "${required_grant}"; then
    echo "runtime MySQL identity is missing the exact DML grant" >&2
    exit 1
fi
if printf '%s\n' "${runtime_grants}" | grep --extended-regexp --quiet \
    'ALL PRIVILEGES|CREATE|ALTER|DROP|TRIGGER|INDEX|REFERENCES|GRANT OPTION'; then
    echo "runtime MySQL identity retains a forbidden schema privilege" >&2
    exit 1
fi

probe_table='runtime_ddl_probe_compose'
mysql_admin --database=commercevision \
    --execute="DROP TABLE IF EXISTS \`${probe_table}\`"
if mysql_runtime \
    --execute="CREATE TABLE \`${probe_table}\` (id INTEGER NOT NULL)" \
    >/dev/null 2>&1; then
    mysql_admin --database=commercevision \
        --execute="DROP TABLE IF EXISTS \`${probe_table}\`"
    echo "runtime MySQL identity unexpectedly created a table" >&2
    exit 1
fi
