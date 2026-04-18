"""
Eksporterer predictions til annotasjonslag.

For hvert (dhlabid, seq_start): velg den lengste token_len om flere overlapper.
Kun PLACE-prediksjoner med pred_geonames_id settes.

Output: annotations_fiction.jsonl  — én rad per unik (dhlabid, seq_start)
Kolonner: dhlabid, seq_start, token_len, geonames_id, confidence, surface

Bruk:
  python export_annotations.py [fiction]
"""

import json
import sqlite3
import sys
from pathlib import Path

DISAMBIG_DB    = Path("geo_disambig.db")
IMAGINATION_DB = Path("~/Github/Dash_Imagination/src/dash_imagination/data/imagination.db").expanduser()


def main():
    fiction_only = len(sys.argv) > 1 and sys.argv[1] == "fiction"

    con = sqlite3.connect(DISAMBIG_DB, timeout=30)

    if fiction_only:
        # Hent fiction-par fra imagination.db
        con_imag = sqlite3.connect(IMAGINATION_DB)
        fiction_pairs = set(con_imag.execute("""
            SELECT DISTINCT b.token, b.geonameid
            FROM books b JOIN corpus c ON b.dhlabid = c.dhlabid
            WHERE c.category LIKE 'Diktning%'
        """).fetchall())
        con_imag.close()
        print(f"Fiction-filter: {len(fiction_pairs):,} (surface, geonames_id)-par")

    # Hent alle PLACE-prediksjoner med konkordanser
    rows = con.execute("""
        SELECT c.dhlabid, c.seq_start, c.token_len,
               p.pred_geonames_id, p.confidence, c.surface, c.geonames_id
        FROM concordances c
        JOIN predictions p ON c.surface = p.surface AND c.geonames_id = p.geonames_id
        WHERE p.label = 'PLACE'
          AND p.pred_geonames_id IS NOT NULL
          AND 1=1  -- subsumed-filter legges til etter mark_subsumed er kjørt
        ORDER BY c.dhlabid, c.seq_start, c.token_len DESC
    """).fetchall()

    if fiction_only:
        rows = [r for r in rows if (r[5], r[6]) in fiction_pairs]

    # Behold bare lengste token_len per (dhlabid, seq_start)
    seen   = {}  # (dhlabid, seq_start) -> rad
    for dhlabid, seq_start, token_len, geonames_id, confidence, surface, orig_gid in rows:
        key = (dhlabid, seq_start)
        if key not in seen:
            seen[key] = {
                "dhlabid":    dhlabid,
                "seq_start":  seq_start,
                "token_len":  token_len,
                "geonames_id": geonames_id,
                "confidence": confidence,
                "surface":    surface,
            }
        # Rader er sortert DESC på token_len, så første treff er alltid lengst

    out = list(seen.values())
    out.sort(key=lambda r: (r["dhlabid"], r["seq_start"]))

    label   = "fiction" if fiction_only else "all"
    outfile = Path(f"annotations_{label}.jsonl")
    outfile.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out),
        encoding="utf-8"
    )

    # Statistikk
    n_books = len({r["dhlabid"] for r in out})
    n_geo   = len({r["geonames_id"] for r in out})
    print(f"Eksportert {len(out):,} annotasjoner")
    print(f"  Bøker:        {n_books:,}")
    print(f"  Unike steder: {n_geo:,}")
    print(f"  Fil:          {outfile}")

    con.close()


if __name__ == "__main__":
    main()
