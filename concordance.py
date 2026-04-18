"""
Bygger LLM-input fra strukturerte KWIC-rader.

Backend returnerer nå renderMode: "structured":
  {"rows": [{"bookId", "seqStart", "len", "before", "hit", "after", "surface"}]}

Ingen parsing nødvendig — data brukes direkte.
"""

import json
import sqlite3
from pathlib import Path

GEO_DB         = Path("geonames.db")
MAX_CANDIDATES = 15
GEONAMES_USER  = "yoonsen"   # gratis bruker på geonames.org


def normalize_token(token: str) -> str:
    """
    Normaliser tokeniserte overflateformer.
    'St . Petersburg' -> 'St. Petersburg'
    'U . S . A .'    -> 'U.S.A.'
    """
    import re
    # fjern mellomrom foran punktum (tokeniserings-artefakt)
    return re.sub(r'\s+\.', '.', token).strip()


def _rows_to_candidates(rows) -> list[dict]:
    return [
        {
            "geonames_id":   r[0],
            "name":          r[1],
            "feature_class": r[2],
            "feature_code":  r[3],
            "country_code":  r[4],
            "lat":           r[5],
            "lon":           r[6],
            "source":        "local",
        }
        for r in rows
    ]


def get_candidates_local(token: str) -> list[dict]:
    """Oppslag i lokal geonames.db via alternates + places.name (moderne navn)."""
    con = sqlite3.connect(GEO_DB)
    rows = con.execute("""
        SELECT DISTINCT p.geonameid, p.name, p.feature_class, p.feature_code,
               p.country_code, p.latitude, p.longitude
        FROM (
            SELECT a.geonameid FROM alternates a WHERE a.alternatename = ?
            UNION
            SELECT p2.geonameid FROM places p2
            WHERE p2.name = ? OR p2.asciiname = ?
        ) ids
        JOIN places p ON ids.geonameid = p.geonameid
        ORDER BY p.population DESC
        LIMIT ?
    """, (token, token, token, MAX_CANDIDATES)).fetchall()
    con.close()
    return _rows_to_candidates(rows)


def get_candidates_api(token: str) -> list[dict]:
    """Fallback: GeoNames search API når lokalt oppslag gir ingenting."""
    import requests
    try:
        resp = requests.get(
            "http://api.geonames.org/searchJSON",
            params={
                "q":        token,
                "maxRows":  MAX_CANDIDATES,
                "username": GEONAMES_USER,
                "style":    "SHORT",
            },
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("geonames", [])
        return [
            {
                "geonames_id":   h["geonameId"],
                "name":          h.get("name", ""),
                "feature_class": h.get("fcl", ""),
                "feature_code":  h.get("fcode", ""),
                "country_code":  h.get("countryCode", ""),
                "lat":           float(h.get("lat", 0)),
                "lon":           float(h.get("lng", 0)),
                "source":        "api",
            }
            for h in hits
        ]
    except Exception as e:
        print(f"    GeoNames API feil for {token!r}: {e}")
        return []


def get_candidates(token: str) -> list[dict]:
    """
    Hent kandidater: lokalt oppslag først, GeoNames API som fallback.
    Normaliserer token (fikser 'St .' -> 'St.') før oppslag.
    """
    norm = normalize_token(token)
    candidates = get_candidates_local(norm)
    if not candidates and norm != token:
        # prøv også original form
        candidates = get_candidates_local(token)
    if not candidates:
        candidates = get_candidates_api(norm)
    return candidates


def build_llm_input(record: dict, max_conc: int = 3) -> dict:
    """
    Bygg komplett LLM-input fra en KWIC-record.
    Forventer strukturerte KWIC-rader med seqStart/len/before/hit/after.
    """
    token = record["token"]

    concordances = [
        {
            "seq_start": row["seqStart"],
            "len":       row["len"],
            "before":    row["before"],
            "hit":       token if row.get("hit", "").lower() == token.lower() else row["hit"],
            "after":     row["after"],
        }
        for row in record.get("kwic", [])[:max_conc]
        if isinstance(row, dict) and "seqStart" in row
    ]

    return {
        "token":    token,
        "metadata": {
            "title":      record.get("title"),
            "author":     record.get("author"),
            "category":   record.get("category"),
            "year":       record.get("year"),
            "translated": bool(record.get("oversatt")),
        },
        "concordances": concordances,
        "candidates":   get_candidates(token),
    }


if __name__ == "__main__":
    records = [json.loads(l) for l in Path("sample_500_kwic.jsonl").read_text().splitlines()]

    for rec in records:
        if rec["kwic"] and isinstance(rec["kwic"][0], dict) and "seqStart" in rec["kwic"][0]:
            print(json.dumps(build_llm_input(rec), ensure_ascii=False, indent=2))
            break
    else:
        print("Ingen strukturerte KWIC-rader ennå — kjør kwic.py på nytt mot ny backend.")
