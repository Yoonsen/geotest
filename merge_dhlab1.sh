#!/usr/bin/env bash
# Merger KWIC-konkordanser fra dhlab1 inn i lokal geo_disambig.db
# Kjøres etter at KWIC-løkken på dhlab1 er ferdig.
#
# Steg:
#   1. Kopierer geo_disambig.db fra dhlab1 til /tmp/
#   2. Merger concordances (INSERT OR IGNORE) og oppdaterer kwic_fetched
#   3. Kjører mark_subsumed
#   4. Rapporterer antall nye konkordanser

set -e

REMOTE="dhlab1.nb.no"
REMOTE_DB="~/geotest/geo_disambig.db"
LOCAL_COPY="/tmp/geo_disambig_dhlab1.db"
LOCAL_DB="geo_disambig.db"

echo "=== Steg 1: Kopierer DB fra dhlab1 ==="
scp "${REMOTE}:${REMOTE_DB}" "${LOCAL_COPY}"

echo "=== Steg 2: Merger concordances ==="
python3 - <<EOF
import sqlite3

local  = sqlite3.connect("${LOCAL_DB}", timeout=60)
local.execute("PRAGMA journal_mode=WAL")

local.execute("ATTACH DATABASE '${LOCAL_COPY}' AS remote")

# Tell konkordanser før
n_before = local.execute("SELECT COUNT(*) FROM concordances").fetchone()[0]

# Merger nye konkordanser
local.execute("""
    INSERT OR IGNORE INTO concordances
        (surface, geonames_id, dhlabid, seq_start, token_len, before, after)
    SELECT surface, geonames_id, dhlabid, seq_start, token_len, before, after
    FROM remote.concordances
""")

# Oppdater kwic_fetched for token_types som nå har konkordans på dhlab1
local.execute("""
    UPDATE token_types SET kwic_fetched = 1
    WHERE kwic_fetched = 0
      AND (surface, geonames_id) IN (
          SELECT DISTINCT surface, geonames_id FROM remote.concordances
      )
""")

local.commit()

n_after = local.execute("SELECT COUNT(*) FROM concordances").fetchone()[0]
n_new   = n_after - n_before
n_fetched = local.execute("SELECT COUNT(*) FROM token_types WHERE kwic_fetched=1").fetchone()[0]
n_total   = local.execute("SELECT COUNT(*) FROM token_types").fetchone()[0]

print(f"Nye konkordanser: {n_new:,}")
print(f"Totalt: {n_after:,} konkordanser")
print(f"kwic_fetched: {n_fetched:,}/{n_total:,} token_types")

local.close()
EOF

echo "=== Steg 3: mark_subsumed ==="
python build_db.py mark_subsumed

echo "=== Ferdig — klar for disambig_prod.py nano2 ==="
python3 - <<EOF
import sqlite3
con = sqlite3.connect("${LOCAL_DB}")
pending = con.execute("""
    SELECT COUNT(*) FROM token_types t
    WHERE t.kwic_fetched = 1
      AND NOT EXISTS (SELECT 1 FROM predictions p
                      WHERE p.surface=t.surface AND p.geonames_id=t.geonames_id)
""").fetchone()[0]
print(f"Token_types klare for disambiguering: {pending:,}")
con.close()
EOF
