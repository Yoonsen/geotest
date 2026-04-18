"""
Postprosessering av disambigueringsresultater.

Regel: om modellen returnerte en A (administrativ enhet), sjekk om det
finnes en P (by/tettsted) i kandidatlisten for samme sted (samme land,
geografisk nær). Hvis ja, bruk P i stedet.

Bruk:
  python postprocess.py results_anthropic.jsonl
  python postprocess.py results_openai.jsonl
"""

import json
import math
import sqlite3
import sys
from pathlib import Path

GEO_DB = Path("geonames.db")
MAX_DIST_KM = 50   # maks avstand for å anse to steder som "samme"


def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def place_info(con, geonames_id):
    if not geonames_id:
        return None
    return con.execute(
        "SELECT name, feature_class, feature_code, country_code, latitude, longitude FROM places WHERE geonameid=?",
        (geonames_id,)
    ).fetchone()


def find_city_alternative(con, pred_id, candidate_ids):
    """
    Gitt en A-entitet (pred_id), finn nærmeste P-entitet blant kandidatene.
    Returnerer (geonames_id, dist_km) eller None.
    """
    pred = place_info(con, pred_id)
    if not pred:
        return None
    _, _, _, pred_country, pred_lat, pred_lon = pred

    best_id, best_dist = None, MAX_DIST_KM
    for cid in candidate_ids:
        if cid == pred_id:
            continue
        row = place_info(con, cid)
        if not row:
            continue
        _, fc, _, country, lat, lon = row
        if fc != "P":
            continue
        if country != pred_country:
            continue
        if lat is None or lon is None:
            continue
        dist = haversine(pred_lat, pred_lon, lat, lon)
        if dist < best_dist:
            best_dist = dist
            best_id   = cid

    return (best_id, best_dist) if best_id else None


def load_candidates(kwic_file: Path) -> dict:
    """Hent GeoNames-kandidater per dhlabid fra kwic-filen via concordance."""
    from concordance import build_llm_input
    candidates = {}
    for line in kwic_file.read_text().splitlines():
        rec = json.loads(line)
        llm_input = build_llm_input(rec)
        candidates[rec["dhlabid"]] = [c["geonames_id"] for c in llm_input["candidates"]]
    return candidates


def main():
    input_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results_anthropic.jsonl")
    output_file = input_file.with_stem(input_file.stem + "_post")

    print(f"Laster kandidater fra sample_500_kwic.jsonl...")
    candidates = load_candidates(Path("sample_500_kwic.jsonl"))

    con = sqlite3.connect(GEO_DB)
    results = [json.loads(l) for l in input_file.read_text().splitlines()]

    fixed = 0
    out   = []

    for r in results:
        new = dict(r)
        gid = r.get("geonames_id")
        if not gid:
            out.append(new)
            continue

        info = place_info(con, gid)
        if not info:
            out.append(new)
            continue

        _, fc, _, _, _, _ = info
        if fc == "A":
            cand_ids = candidates.get(r["dhlabid"], [])
            alt = find_city_alternative(con, gid, cand_ids)
            if alt:
                alt_id, dist = alt
                new["geonames_id"]    = alt_id
                new["postprocessed"]  = f"A→P: {gid}→{alt_id} ({dist:.0f}km)"
                fixed += 1

        out.append(new)

    con.close()

    output_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out),
        encoding="utf-8"
    )
    print(f"Ferdig: {output_file}")
    print(f"Endret {fixed}/{len(results)} (A→P)")


if __name__ == "__main__":
    main()
