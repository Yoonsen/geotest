"""
Bygger imagination_v2.db:
  - Kopierer alle tabeller fra imagination.db
  - Legger til geo_annotations gruppert på (dhlabid, geonames_id)
    med frekvenstelling og GeoNames-metadata

Bruk:
  python build_imagination_v2.py [fiction]
"""

import shutil
import sqlite3
import sys
from pathlib import Path

IMAGINATION_DB  = Path("~/Github/Dash_Imagination/src/dash_imagination/data/imagination.db").expanduser()
IMAGINATION_V2  = Path("~/Github/Dash_Imagination/src/dash_imagination/data/imagination_v2.db").expanduser()
ANNOTATIONS_DB  = Path("annotations.db")
GEO_DB          = Path("geonames.db")

GEO_SCHEMA = """
CREATE TABLE IF NOT EXISTS geo_annotations (
    dhlabid       INTEGER NOT NULL,
    geonames_id   INTEGER NOT NULL,
    surface       TEXT,
    name          TEXT,
    feature_class TEXT,
    feature_code  TEXT,
    country_code  TEXT,
    lat           REAL,
    lon           REAL,
    count         INTEGER DEFAULT 1,
    confidence    REAL,
    model         TEXT,
    PRIMARY KEY (dhlabid, geonames_id)
);
CREATE INDEX IF NOT EXISTS idx_geo_ann_dhlabid   ON geo_annotations(dhlabid);
CREATE INDEX IF NOT EXISTS idx_geo_ann_geonames  ON geo_annotations(geonames_id);
CREATE INDEX IF NOT EXISTS idx_geo_ann_country   ON geo_annotations(country_code);
CREATE INDEX IF NOT EXISTS idx_geo_ann_fclass    ON geo_annotations(feature_class);
"""


def main():
    fiction_only = len(sys.argv) > 1 and sys.argv[1] == "fiction"

    # Kopier imagination.db → imagination_v2.db
    print(f"Kopierer {IMAGINATION_DB.name} → {IMAGINATION_V2.name}...")
    shutil.copy2(IMAGINATION_DB, IMAGINATION_V2)

    con_v2  = sqlite3.connect(IMAGINATION_V2, timeout=30)
    con_ann = sqlite3.connect(ANNOTATIONS_DB, timeout=30)
    con_geo = sqlite3.connect(GEO_DB, timeout=30)

    con_v2.execute("PRAGMA journal_mode=WAL")
    con_v2.executescript(GEO_SCHEMA)

    # Grupper annotations på (dhlabid, geonames_id)
    print("Grupperer annotasjoner...")
    rows = con_ann.execute("""
        SELECT dhlabid, geonames_id,
               surface,
               COUNT(*)       AS count,
               AVG(confidence) AS avg_conf,
               MAX(model)     AS model
        FROM annotations
        GROUP BY dhlabid, geonames_id
        ORDER BY dhlabid, count DESC
    """).fetchall()

    if fiction_only:
        con_imag = sqlite3.connect(IMAGINATION_DB)
        fiction_dhlabids = {r[0] for r in con_imag.execute("""
            SELECT dhlabid FROM corpus WHERE category LIKE 'Diktning%'
        """).fetchall()}
        con_imag.close()
        rows = [r for r in rows if r[0] in fiction_dhlabids]
        print(f"Fiction-filter: {len(fiction_dhlabids):,} bøker")

    print(f"Slår opp GeoNames-metadata for {len({r[1] for r in rows}):,} unike steder...")
    geo_cache = {}
    for gid in {r[1] for r in rows}:
        row = con_geo.execute("""
            SELECT name, feature_class, feature_code, country_code, latitude, longitude
            FROM places WHERE geonameid = ?
        """, (gid,)).fetchone()
        geo_cache[gid] = row or (None, None, None, None, None, None)

    # Skriv til geo_annotations
    batch = []
    for dhlabid, geonames_id, surface, count, avg_conf, model in rows:
        name, fc, fcode, cc, lat, lon = geo_cache[geonames_id]
        batch.append((dhlabid, geonames_id, surface, name, fc, fcode, cc,
                      lat, lon, count, round(avg_conf, 3) if avg_conf else None, model))

    con_v2.executemany("""
        INSERT OR REPLACE INTO geo_annotations
            (dhlabid, geonames_id, surface, name, feature_class, feature_code,
             country_code, lat, lon, count, confidence, model)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, batch)
    con_v2.commit()

    # Statistikk
    n_books  = len({r[0] for r in rows})
    n_places = len({r[1] for r in rows})
    print(f"\nFerdig: {IMAGINATION_V2}")
    print(f"  Rader i geo_annotations: {len(batch):,}")
    print(f"  Bøker:                   {n_books:,}")
    print(f"  Unike steder:            {n_places:,}")

    con_v2.close()
    con_ann.close()
    con_geo.close()


if __name__ == "__main__":
    main()
