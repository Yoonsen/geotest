"""
Bygg geonames.db fra GeoNames-filer.

Tabeller:
  places      — fra allCountries.txt
  alternates  — fra alternateNamesV2.txt (kun geonameid + alternateName)

Kjøretid: ~5-10 min på full dump.
"""

import sqlite3
import time
from pathlib import Path

ALL_COUNTRIES   = Path("allCountries.txt")
ALTERNATE_NAMES = Path("alternateNamesV2.txt")
DB_PATH         = Path("geonames.db")

BATCH = 100_000


def create_schema(con: sqlite3.Connection):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS places (
            geonameid     INTEGER PRIMARY KEY,
            name          TEXT,
            asciiname     TEXT,
            latitude      REAL,
            longitude     REAL,
            feature_class TEXT,
            feature_code  TEXT,
            country_code  TEXT,
            population    INTEGER
        );

        CREATE TABLE IF NOT EXISTS alternates (
            geonameid     INTEGER NOT NULL,
            alternatename TEXT NOT NULL
        );
    """)
    con.commit()


def import_places(con: sqlite3.Connection):
    print(f"Importerer {ALL_COUNTRIES} ...")
    t0 = time.time()
    n  = 0

    con.execute("DELETE FROM places")
    con.commit()

    batch = []
    with open(ALL_COUNTRIES, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 15:
                continue
            batch.append((
                int(parts[0]),   # geonameid
                parts[1],        # name
                parts[2],        # asciiname
                float(parts[4]) if parts[4] else None,  # latitude
                float(parts[5]) if parts[5] else None,  # longitude
                parts[6],        # feature_class
                parts[7],        # feature_code
                parts[8],        # country_code
                int(parts[14]) if parts[14] else 0,     # population
            ))
            if len(batch) >= BATCH:
                con.executemany(
                    "INSERT OR REPLACE INTO places VALUES (?,?,?,?,?,?,?,?,?)",
                    batch
                )
                con.commit()
                n += len(batch)
                batch = []
                print(f"  {n:,} steder...", end="\r")

    if batch:
        con.executemany(
            "INSERT OR REPLACE INTO places VALUES (?,?,?,?,?,?,?,?,?)",
            batch
        )
        con.commit()
        n += len(batch)

    print(f"  {n:,} steder importert ({time.time()-t0:.0f}s)")


def import_alternates(con: sqlite3.Connection):
    print(f"Importerer {ALTERNATE_NAMES} ...")
    t0 = time.time()
    n  = 0

    con.execute("DELETE FROM alternates")
    con.commit()

    batch = []
    with open(ALTERNATE_NAMES, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            geonameid     = parts[1]
            alternatename = parts[3]
            if not geonameid or not alternatename:
                continue
            batch.append((int(geonameid), alternatename))

            if len(batch) >= BATCH:
                con.executemany(
                    "INSERT INTO alternates VALUES (?,?)",
                    batch
                )
                con.commit()
                n += len(batch)
                batch = []
                print(f"  {n:,} alternativnavn...", end="\r")

    if batch:
        con.executemany("INSERT INTO alternates VALUES (?,?)", batch)
        con.commit()
        n += len(batch)

    print(f"  {n:,} alternativnavn importert ({time.time()-t0:.0f}s)")


def create_indexes(con: sqlite3.Connection):
    print("Bygger indekser ...")
    t0 = time.time()
    con.executescript("""
        CREATE INDEX IF NOT EXISTS idx_alternates_name
            ON alternates(alternatename);
        CREATE INDEX IF NOT EXISTS idx_alternates_geonameid
            ON alternates(geonameid);
        CREATE INDEX IF NOT EXISTS idx_places_country
            ON places(country_code);
    """)
    con.commit()
    print(f"  Indekser ferdig ({time.time()-t0:.0f}s)")


def main():
    print(f"Bygger {DB_PATH} ...")
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-512000")  # 512 MB cache

    create_schema(con)
    import_places(con)
    import_alternates(con)
    create_indexes(con)
    con.close()

    size = DB_PATH.stat().st_size / 1e9
    print(f"\nFerdig: {DB_PATH} ({size:.1f} GB)")


if __name__ == "__main__":
    main()
