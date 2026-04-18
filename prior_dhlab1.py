"""
Prior-disambiguering for dhlab1 med Qwen3.5 Q8.
Leser token_types fra prior_nonfiction.jsonl (pre-generert lokalt).

Bruk:
  python prior_dhlab1.py [prior_nonfiction.jsonl]
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

Q8_BASE_URL = "http://localhost:9090/v1"
Q8_MODEL    = "qwen3.5-27b-q8"

DISAMBIG_DB = Path("priors_dhlab1.db")
GEO_DB      = Path("geonames.db")

PRIORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS priors (
    surface          TEXT    NOT NULL,
    geonames_id      INTEGER NOT NULL,
    label            TEXT,
    pred_geonames_id INTEGER,
    confidence       REAL,
    model            TEXT,
    elapsed_s        REAL,
    PRIMARY KEY (surface, geonames_id)
);
CREATE INDEX IF NOT EXISTS idx_priors_pred ON priors(pred_geonames_id);
"""

SYSTEM_PROMPT = """\
Du er en geografisk disambiguerer for norsk 1800-tallstekst.
Avgjør om kandidatordet er et stedsnavn i denne boken, og hvilket GeoNames-sted det mest sannsynlig refererer til gitt bokkonteksten.

Returner kun gyldig JSON:
{
  "label": "PLACE" | "PERSON" | "OTHER",
  "geonames_id": <heltall eller null>,
  "confidence": <0.0–1.0>
}

Regler:
- label "PLACE" kun om det faktisk er et geografisk sted
- geonames_id skal matche én av kandidatene om mulig, ellers null
- confidence 1.0 = helt sikker, 0.0 = rent gjett
- Du har ikke tilgang til selve teksten — bruk tittel, forfatter og sjanger som kontekst
"""


def get_candidates(geo_con, surface: str) -> list[dict]:
    norm = surface.lower().strip()
    rows = geo_con.execute("""
        SELECT DISTINCT p.geonameid, p.name, p.feature_class, p.feature_code,
                        p.country_code, p.latitude, p.longitude
        FROM places p
        LEFT JOIN alternates a ON p.geonameid = a.geonameid
        WHERE lower(p.name) = ? OR lower(p.asciiname) = ? OR lower(a.alternatename) = ?
        ORDER BY p.population DESC
        LIMIT 15
    """, (norm, norm, norm)).fetchall()
    return [{"geonames_id": r[0], "name": r[1], "feature_class": r[2],
             "feature_code": r[3], "country_code": r[4] or "",
             "lat": r[5] or 0.0, "lon": r[6] or 0.0} for r in rows]


def build_prompt(surface, title, author, category, year, candidates):
    lines = []
    if title:  lines.append(f"Tittel: {title!r}")
    if author: lines.append(f"Forfatter: {author}")
    if category: lines.append(f"Sjanger: {category}")
    if year:   lines.append(f"År: {year}")
    lines += ["", f'Token: "{surface}"', ""]
    if candidates:
        lines.append("GeoNames-kandidater:")
        for c in candidates:
            lines.append(f'  {c["geonames_id"]} | {c["name"]} | '
                         f'{c["feature_class"]}/{c["feature_code"]} | '
                         f'{c["country_code"]} | {c["lat"]:.4f},{c["lon"]:.4f}')
    else:
        lines.append("GeoNames-kandidater: ingen funnet")
    return "\n".join(lines)


def parse_json(text: str) -> dict:
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:]).rstrip("`").strip()
    s, e = text.find("{"), text.rfind("}") + 1
    if s == -1:
        raise ValueError(f"Ingen JSON: {text[:80]}")
    return json.loads(text[s:e])


def call_q8(prompt: str) -> tuple[dict, float]:
    import requests
    raw = (f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
           f"<|im_start|>user\n{prompt}<|im_end|>\n"
           f"<|im_start|>assistant\n<think>\n</think>\n")
    t0 = time.time()
    resp = requests.post(Q8_BASE_URL + "/completions",
                         json={"prompt": raw, "max_tokens": 128,
                               "temperature": 0.0, "stop": ["<|im_end|>"]},
                         timeout=60)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["text"].strip()
    return parse_json(text), time.time() - t0


def main():
    input_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("prior_nonfiction.jsonl")
    rows = [json.loads(l) for l in input_file.read_text().splitlines() if l.strip()]

    con = sqlite3.connect(DISAMBIG_DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(PRIORS_SCHEMA)

    geo_con = sqlite3.connect(GEO_DB, timeout=30)

    # Ekskluder allerede disambiguerte
    done_set = set(con.execute("SELECT surface, geonames_id FROM priors").fetchall())
    rows = [r for r in rows if (r["surface"], r["geonames_id"]) not in done_set]

    print(f"Prior Q8: {len(rows):,} token_types gjenstår")

    errors = 0
    t_start = time.time()

    for i, rec in enumerate(rows, 1):
        surface     = rec["surface"]
        geonames_id = rec["geonames_id"]
        candidates  = get_candidates(geo_con, surface)
        prompt      = build_prompt(surface, rec.get("title"), rec.get("author"),
                                   rec.get("category"), rec.get("year"), candidates)
        try:
            answer, elapsed = call_q8(prompt)
        except Exception as e:
            print(f"  [{i:6d}/{len(rows)}] {surface!r:28s} FEIL: {e}")
            answer  = {"label": None, "geonames_id": None, "confidence": None}
            elapsed = 0.0
            errors += 1

        con.execute("""
            INSERT OR REPLACE INTO priors
                (surface, geonames_id, label, pred_geonames_id, confidence, model, elapsed_s)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (surface, geonames_id, answer.get("label"), answer.get("geonames_id"),
              answer.get("confidence"), Q8_MODEL, round(elapsed, 2)))

        if i % 100 == 0:
            con.commit()
            rate    = i / (time.time() - t_start)
            eta_h   = (len(rows) - i) / rate / 3600
            print(f"  [{i:6d}/{len(rows)}] {errors} feil, {rate:.2f}/s, ETA {eta_h:.1f}t",
                  flush=True)

        label = str(answer.get("label") or "?")
        pred  = answer.get("geonames_id")
        print(f"  [{i:6d}/{len(rows)}] {surface!r:28s} → {label:6s} id={str(pred):10} ({elapsed:.1f}s)")

    con.commit()
    con.close()
    geo_con.close()

    elapsed_total = (time.time() - t_start) / 3600
    print(f"\nFerdig: {len(rows):,} priors, {errors} feil, {elapsed_total:.1f}t totalt")


if __name__ == "__main__":
    main()
