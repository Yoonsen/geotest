"""
Stedsoppslag og fallback-håndtering.

Logikk:
  - Slår opp geonameid i geo-tabellen (geo_norsk.db)
  - Om ikke funnet: tildel negativ lokal ID og lagre i local_places.jsonl
  - geonameid > 0  => ekte GeoNames
  - geonameid < 0  => lokalt generert, ikke i GeoNames
"""

import json
import sqlite3
from pathlib import Path

GEO_DB = Path("~/Github/geo_loc_disambig/geo_norsk.db").expanduser()
LOCAL_PLACES_FILE = Path("local_places.jsonl")


def _load_local_places() -> dict[str, dict]:
    """Les inn eksisterende lokale steder, indeksert på token."""
    places = {}
    if LOCAL_PLACES_FILE.exists():
        with open(LOCAL_PLACES_FILE, encoding="utf-8") as f:
            for line in f:
                p = json.loads(line)
                places[p["token"]] = p
    return places


def _next_local_id(local_places: dict) -> int:
    """Neste ledige negative ID."""
    if not local_places:
        return -1
    return min(p["geonameid"] for p in local_places.values()) - 1


def _save_local_place(place: dict):
    with open(LOCAL_PLACES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(place, ensure_ascii=False) + "\n")


def lookup_place(token: str, geonameid: int | None) -> dict:
    """
    Slå opp sted fra GeoNames. Om geonameid mangler eller ikke finnes
    i databasen, opprett lokal oppføring med negativ ID.

    Returnerer dict med feltene:
      geonameid, name, latitude, longitude,
      feature_class, feature_code, country_code, id_source
    """
    # forsøk GeoNames-oppslag
    if geonameid is not None:
        con = sqlite3.connect(GEO_DB)
        row = con.execute("""
            SELECT geonameid, name, latitude, longitude,
                   "feature class", "feature code", "country code"
            FROM geo WHERE geonameid = ?
        """, (geonameid,)).fetchone()
        con.close()

        if row:
            return {
                "geonameid":     row[0],
                "name":          row[1],
                "latitude":      row[2],
                "longitude":     row[3],
                "feature_class": row[4],
                "feature_code":  row[5],
                "country_code":  row[6],
                "id_source":     "geonames",
            }

    # fallback: lokalt sted
    local_places = _load_local_places()

    if token in local_places:
        return local_places[token]

    new_id = _next_local_id(local_places)
    place = {
        "geonameid":     new_id,
        "name":          token,
        "latitude":      None,
        "longitude":     None,
        "feature_class": None,
        "feature_code":  None,
        "country_code":  None,
        "id_source":     "local",
        "token":         token,
    }
    _save_local_place(place)
    return place


if __name__ == "__main__":
    # liten test
    result = lookup_place("Bergen", 3161732)
    print("GeoNames-treff:", result)

    result = lookup_place("Ukjentdalen", None)
    print("Lokal fallback:", result)

    result = lookup_place("Ukjentdalen", None)
    print("Lokal gjenbruk:", result)
