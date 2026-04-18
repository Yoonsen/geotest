"""
Bygger annotasjonstabell i annotations.db med full GeoNames-metadata.
Eksporterer også til annotations_fiction.json.

Bruk:
  python build_annotations_db.py [fiction]
"""

import json
import sqlite3
import sys
from pathlib import Path

DISAMBIG_DB    = Path("geo_disambig.db")
GEO_DB         = Path("geonames.db")
ANNOTATIONS_DB = Path("annotations.db")
IMAGINATION_DB = Path("~/Github/Dash_Imagination/src/dash_imagination/data/imagination.db").expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS annotations (
    dhlabid       INTEGER NOT NULL,
    seq_start     INTEGER NOT NULL,
    token_len     INTEGER NOT NULL DEFAULT 1,
    surface       TEXT,
    geonames_id   INTEGER,
    name          TEXT,
    feature_class TEXT,
    feature_code  TEXT,
    country_code  TEXT,
    lat           REAL,
    lon           REAL,
    confidence    REAL,
    model         TEXT,
    PRIMARY KEY (dhlabid, seq_start)
);
CREATE INDEX IF NOT EXISTS idx_ann_geonames ON annotations(geonames_id);
CREATE INDEX IF NOT EXISTS idx_ann_book     ON annotations(dhlabid);
CREATE INDEX IF NOT EXISTS idx_ann_country  ON annotations(country_code);
CREATE INDEX IF NOT EXISTS idx_ann_fclass   ON annotations(feature_class);
"""


def main():
    fiction_only = len(sys.argv) > 1 and sys.argv[1] == "fiction"

    con_dis = sqlite3.connect(DISAMBIG_DB, timeout=30)
    con_geo = sqlite3.connect(GEO_DB, timeout=30)
    con_ann = sqlite3.connect(ANNOTATIONS_DB, timeout=30)
    con_ann.execute("PRAGMA journal_mode=WAL")
    con_ann.executescript(SCHEMA)

    if fiction_only:
        con_imag = sqlite3.connect(IMAGINATION_DB)
        fiction_pairs = set(con_imag.execute("""
            SELECT DISTINCT b.token, b.geonameid
            FROM books b JOIN corpus c ON b.dhlabid = c.dhlabid
            WHERE c.category LIKE 'Diktning%'
        """).fetchall())
        con_imag.close()
        print(f"Fiction-filter: {len(fiction_pairs):,} par")

    # Hent predictions + konkordanser
    rows = con_dis.execute("""
        SELECT c.dhlabid, c.seq_start, c.token_len,
               c.surface, p.pred_geonames_id, p.confidence, p.model
        FROM concordances c
        JOIN predictions p ON c.surface = p.surface AND c.geonames_id = p.geonames_id
        WHERE p.label = 'PLACE'
          AND p.pred_geonames_id IS NOT NULL
        ORDER BY c.dhlabid, c.seq_start, c.token_len DESC
    """).fetchall()

    if fiction_only:
        rows = [r for r in rows if (r[3], r[4]) in fiction_pairs
                or any((r[3], gid) in fiction_pairs for gid in [r[4]])]
        # Enklere: filtrer på om surface er i fiction
        fiction_surfaces = {s for s, _ in fiction_pairs}
        rows = [r for r in rows if r[3] in fiction_surfaces]

    # Behold lengste token_len per (dhlabid, seq_start)
    seen = {}
    for dhlabid, seq_start, token_len, surface, geonames_id, confidence, model in rows:
        key = (dhlabid, seq_start)
        if key not in seen:
            seen[key] = (dhlabid, seq_start, token_len, surface, geonames_id, confidence, model)

    print(f"Unike posisjoner: {len(seen):,}")

    # Slå opp GeoNames-metadata i batch
    geo_cache = {}
    unique_gids = {v[4] for v in seen.values()}
    print(f"Slår opp {len(unique_gids):,} unike GeoNames-IDer...")
    for gid in unique_gids:
        row = con_geo.execute("""
            SELECT name, feature_class, feature_code, country_code, latitude, longitude
            FROM places WHERE geonameid = ?
        """, (gid,)).fetchone()
        if row:
            geo_cache[gid] = row
        else:
            geo_cache[gid] = (None, None, None, None, None, None)

    # Skriv til annotations.db
    con_ann.execute("DELETE FROM annotations")  # full rebuild
    batch = []
    for dhlabid, seq_start, token_len, surface, geonames_id, confidence, model in seen.values():
        name, fc, fcode, cc, lat, lon = geo_cache.get(geonames_id, (None,)*6)
        batch.append((dhlabid, seq_start, token_len, surface, geonames_id,
                      name, fc, fcode, cc, lat, lon, confidence, model))

    con_ann.executemany("""
        INSERT OR REPLACE INTO annotations
            (dhlabid, seq_start, token_len, surface, geonames_id,
             name, feature_class, feature_code, country_code, lat, lon,
             confidence, model)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, batch)
    con_ann.commit()
    print(f"Skrevet {len(batch):,} rader til {ANNOTATIONS_DB}")

    # Eksporter til JSON
    label   = "fiction" if fiction_only else "all"
    outfile = Path(f"annotations_{label}.json")
    out = []
    for (dhlabid, seq_start, token_len, surface, geonames_id,
         name, fc, fcode, cc, lat, lon, confidence, model) in batch:
        out.append({
            "dhlabid":       dhlabid,
            "seq_start":     seq_start,
            "token_len":     token_len,
            "surface":       surface,
            "geonames_id":   geonames_id,
            "name":          name,
            "feature_class": fc,
            "feature_code":  fcode,
            "country_code":  cc,
            "lat":           lat,
            "lon":           lon,
            "confidence":    confidence,
            "model":         model,
        })
    out.sort(key=lambda r: (r["dhlabid"], r["seq_start"]))
    outfile.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    n_books  = len({r["dhlabid"] for r in out})
    n_places = len({r["geonames_id"] for r in out})
    print(f"Eksportert til {outfile} ({outfile.stat().st_size/1e6:.1f} MB)")
    print(f"  Bøker:        {n_books:,}")
    print(f"  Unike steder: {n_places:,}")

    con_dis.close()
    con_geo.close()
    con_ann.close()


if __name__ == "__main__":
    main()
