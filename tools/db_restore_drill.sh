#!/bin/sh
# The restore DRILL (Phase 6 / C3): prove a backup restores, with numbers.
#
#   tools/db_restore_drill.sh "postgresql://user:pw@host:port/dbname"
#
# A backup that has never been restored is a hope. This script is the other half
# of the db-backup compose service: it dumps the named database, restores the
# dump into a scratch database on the same server, compares the ROW COUNT OF
# EVERY TABLE between source and restore, and drops the scratch database. Output
# is counts only — table names and row counts, no cell values.
#
# Run it quarterly, and after any Postgres upgrade. Paste the verdict block into
# docs/09-OPERATIONS.md's drill log.
set -eu

URL="${1:?usage: db_restore_drill.sh postgresql://user:pw@host:port/dbname}"

# Prefer the local binaries zip (%LOCALAPPDATA%\recon-pg) so the drill uses the
# same major version as the server it talks to.
if [ -n "${LOCALAPPDATA:-}" ] && [ -x "$LOCALAPPDATA/recon-pg/pgsql/bin/pg_dump.exe" ]; then
    PGBIN="$LOCALAPPDATA/recon-pg/pgsql/bin"
    PGDUMP="$PGBIN/pg_dump.exe"; PGRESTORE="$PGBIN/pg_restore.exe"; PSQL="$PGBIN/psql.exe"
else
    PGDUMP=pg_dump; PGRESTORE=pg_restore; PSQL=psql
fi

STAMP=$(date +%Y%m%d-%H%M%S)
DRILL_DB="recon_drill_$STAMP"
TMP="${TMPDIR:-/tmp}/recon-drill-$STAMP.dump"
BASE_URL=$(printf '%s' "$URL" | sed 's|/[^/]*$||')
SOURCE_DB=$(printf '%s' "$URL" | sed 's|.*/||' | sed 's|?.*||')

COUNT_SQL="select relname, n_live_tup from pg_stat_user_tables order by relname"
EXACT_SQL="
select relname || '=' || (xpath('/row/cnt/text()',
    query_to_xml('select count(*) as cnt from ' || quote_ident(relname), false, true, '')
))[1]::text
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
order by relname"

echo "== drill: dumping $SOURCE_DB"
"$PGDUMP" -d "$URL" -Fc -f "$TMP"
SIZE=$(wc -c < "$TMP")
echo "   dump: $TMP ($SIZE bytes)"

echo "== drill: restoring into $DRILL_DB"
"$PSQL" -d "$BASE_URL/postgres" -q -c "create database \"$DRILL_DB\""
trap '"$PSQL" -d "$BASE_URL/postgres" -q -c "drop database if exists \"$DRILL_DB\""; rm -f "$TMP"' EXIT
"$PGRESTORE" -d "$BASE_URL/$DRILL_DB" --no-owner --no-privileges "$TMP"

echo "== drill: comparing exact per-table row counts"
SRC=$("$PSQL" -d "$URL" -At -c "$EXACT_SQL")
DST=$("$PSQL" -d "$BASE_URL/$DRILL_DB" -At -c "$EXACT_SQL")

if [ "$SRC" = "$DST" ]; then
    TABLES=$(printf '%s\n' "$SRC" | wc -l | tr -d ' ')
    ROWS=$(printf '%s\n' "$SRC" | awk -F= '{s+=$2} END {print s}')
    echo "== drill PASSED: $TABLES tables, $ROWS rows, every per-table count identical"
    echo "   ($SOURCE_DB -> $DRILL_DB via pg_dump -Fc, $SIZE bytes)"
    exit 0
fi

echo "== drill FAILED: per-table counts differ"
echo "--- source"; printf '%s\n' "$SRC"
echo "--- restored"; printf '%s\n' "$DST"
exit 1
