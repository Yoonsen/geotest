"""
Runde 2: Forsøk å løse opp annotasjoner med review_state='unresolved'.

Strategi (lokalt, ingen API-avhengighet):
  1. Genitiv-stripping: "Norges" → "Norge", "Englands" → "England" osv.
  2. Lokal geonames.db-søk på strippet form
  3. Om treff: oppdater geonames_id og sett review_state='resolved'
  4. Prøv også LLM-reasoning for foreslått stedsnavn → lokal søk
  5. Valgfritt: fuzzy GeoNames API-søk om brukernavn er satt

Output: annotations.jsonl oppdatert in-place
"""

import json
import re
import sqlite3
from pathlib import Path
from places import lookup_place

ANNOTATIONS   = Path("annotations.jsonl")
GEO_DB        = Path("geonames.db")

# Sett til gyldig brukernavn for å aktivere API-fallback, ellers None
GEONAMES_USER = None   # "yoonsen"  ← registrer på geonames.org og aktiver web services


def strip_genitive(token: str) -> str | None:
    """
    Returner genitiv-strippet form, eller None om ikke relevant.
    Norsk genitiv er alltid bare -s: Norges→Norge, Englands→England, Roms→Rom.
    """
    # Ikke strip korte ord — risikerer falske treff
    if len(token) < 5:
        return None
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return None


def local_lookup(token: str) -> list[dict]:
    """Slå opp token i lokal geonames.db via alternates-tabell."""
    con = sqlite3.connect(GEO_DB)
    rows = con.execute("""
        SELECT DISTINCT p.geonameid, p.name, p.feature_class, p.country_code, p.population
        FROM alternates a
        JOIN places p ON a.geonameid = p.geonameid
        WHERE a.alternatename = ?
        ORDER BY p.population DESC
        LIMIT 3
    """, (token,)).fetchall()
    con.close()
    return [
        {"geonameid": r[0], "name": r[1], "feature_class": r[2],
         "country_code": r[3], "population": r[4]}
        for r in rows
    ]


def extract_suggested_name(reasoning: str) -> str | None:
    """Trekk ut foreslått stedsnavn fra LLM-reasoning."""
    if not reasoning:
        return None
    patterns = [
        r'sannsynlig(?:vis)?\s+([A-ZÆØÅ][a-zæøå]+(?:\s+[A-ZÆØÅ][a-zæøå]+)?)',
        r'trolig\s+([A-ZÆØÅ][a-zæøå]+(?:\s+[A-ZÆØÅ][a-zæøå]+)?)',
        r'(?:er|refererer til)\s+([A-ZÆØÅ][a-zæøå]+(?:\s+[A-ZÆØÅ][a-zæøå]+)?)',
        r'OCR.{0,20}for\s+([A-ZÆØÅ][a-zæøå]+)',
    ]
    for pat in patterns:
        m = re.search(pat, reasoning)
        if m:
            return m.group(1)
    return None


def fuzzy_api_search(query: str) -> list[dict]:
    """GeoNames fuzzy API — bare om GEONAMES_USER er satt."""
    if not GEONAMES_USER:
        return []
    try:
        import requests
        resp = requests.get(
            "http://api.geonames.org/searchJSON",
            params={"q": query, "fuzzy": 0.8, "maxRows": 3,
                    "username": GEONAMES_USER, "style": "SHORT"},
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("geonames", [])
        return [{"geonameid": h["geonameId"], "name": h.get("name", ""),
                 "feature_class": h.get("fcl", ""), "country_code": h.get("countryCode", "")}
                for h in hits]
    except Exception as e:
        print(f"  GeoNames API feil: {e}")
        return []


def resolve_one(canonical: str, reasoning: str) -> dict | None:
    """
    Prøv alle strategier. Returnerer beste treff, eller None.
    Genitiv prøves først — "Norges" → "Norge" (NO) slår ut "Norges-la-Ville" (FR).
    """
    # 1) genitiv-stripping — prøves FØR direkte oppslag for å unngå falske treff
    #    ("Norges" → "Norges-la-Ville" FR vs. "Norge" → Kingdom of Norway)
    stripped = strip_genitive(canonical)
    if stripped:
        hits = local_lookup(stripped)
        if hits:
            return hits[0]

    # 2) direkte lokal oppslag på canonical
    hits = local_lookup(canonical)
    if hits:
        return hits[0]

    # 3) LLM-foreslått navn
    suggested = extract_suggested_name(reasoning)
    if suggested and suggested.lower() != canonical.lower():
        hits = local_lookup(suggested)
        if hits:
            return hits[0]
        # også genitiv av forslag
        s2 = strip_genitive(suggested)
        if s2:
            hits = local_lookup(s2)
            if hits:
                return hits[0]

    # 4) fuzzy API (bare om konto er registrert)
    if GEONAMES_USER:
        for q in filter(None, [canonical, stripped, suggested]):
            hits = fuzzy_api_search(q)
            if hits:
                return hits[0]

    return None


def main():
    annotations = [json.loads(l) for l in ANNOTATIONS.read_text().splitlines()]

    unresolved = [a for a in annotations if a.get("review_state") == "unresolved"]
    print(f"Uløste annotasjoner: {len(unresolved)}")

    resolved = 0
    for ann in unresolved:
        surface   = ann["surface"]
        canonical = ann["canonical"]
        reasoning = ann.get("reasoning", "")

        hit = resolve_one(canonical, reasoning)

        if hit:
            ann["geonames_id"]   = hit["geonameid"]
            ann["feature_class"] = hit.get("feature_class", "")
            ann["country_code"]  = hit.get("country_code", "")
            ann["review_state"]  = "resolved"
            resolved += 1
            print(f"  ✓ {surface!r:25s} → {hit['name']} ({hit.get('country_code','')}) id={hit['geonameid']}")
        else:
            local = lookup_place(canonical, None)
            ann["geonames_id"]  = local["geonameid"]
            ann["review_state"] = "unresolved"
            print(f"  ✗ {surface!r:25s}")

    ANNOTATIONS.write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in annotations),
        encoding="utf-8"
    )

    print(f"\nLøst: {resolved}/{len(unresolved)}")
    states = {}
    for a in annotations:
        s = a.get("review_state", "?")
        states[s] = states.get(s, 0) + 1
    for s, n in sorted(states.items()):
        print(f"  {s}: {n}")


if __name__ == "__main__":
    main()
