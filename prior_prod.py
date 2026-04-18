"""
Boknivå-prior: disambiguerer stedsnavn basert på boktittel + forfatter alene,
uten konkordans. Bruker rep_dhlabid sin tittel/forfatter som kontekst.

Enhet: (surface, geonames_id) — samme som token_types/predictions.
Resultat lagres i tabellen `priors` i geo_disambig.db.

Bruk:
  python prior_prod.py haiku [fiction]   → Haiku, alle eller bare fiction
  python prior_prod.py q8    [fiction]   → Qwen3.5 Q8 på dhlab1
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

from concordance import get_candidates_local, normalize_token

DISAMBIG_DB    = Path("geo_disambig.db")
IMAGINATION_DB = Path("~/Github/Dash_Imagination/src/dash_imagination/data/imagination.db").expanduser()

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
Q8_BASE_URL     = "http://dhlab1.nb.no:9090/v1"
Q8_MODEL        = "qwen3.5-27b-q8"

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


def build_prompt(surface: str, title: str, author: str,
                 category: str, year: int, candidates: list[dict]) -> str:
    lines = []
    if title:
        lines.append(f"Tittel: {title!r}")
    if author:
        lines.append(f"Forfatter: {author}")
    if category:
        lines.append(f"Sjanger: {category}")
    if year:
        lines.append(f"År: {year}")
    lines += ["", f'Token: "{surface}"', ""]

    if candidates:
        lines.append("GeoNames-kandidater:")
        for c in candidates:
            lines.append(
                f'  {c["geonames_id"]} | {c["name"]} | '
                f'{c["feature_class"]}/{c["feature_code"]} | '
                f'{c["country_code"]} | {c["lat"]:.4f},{c["lon"]:.4f}'
            )
    else:
        lines.append("GeoNames-kandidater: ingen funnet")

    return "\n".join(lines)


def parse_json(text: str) -> dict:
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        text = "\n".join(lines).rstrip("`").strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1:
        raise ValueError(f"Ingen JSON: {text[:80]}")
    return json.loads(text[start:end])


def call_haiku(client, prompt: str) -> tuple[dict, float]:
    t0 = time.time()
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=128,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    return parse_json(text), time.time() - t0


def call_q8(prompt: str) -> tuple[dict, float]:
    import requests
    raw = (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n{prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n</think>\n"
    )
    t0 = time.time()
    resp = requests.post(
        Q8_BASE_URL.rstrip("/") + "/completions",
        json={"prompt": raw, "max_tokens": 128, "temperature": 0.0,
              "stop": ["<|im_end|>"]},
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["text"].strip()
    return parse_json(text), time.time() - t0


def load_fiction_pairs() -> set[tuple[str, int]]:
    """Hent alle (token, geonameid)-par fra Diktning-kategoriene."""
    con = sqlite3.connect(IMAGINATION_DB)
    pairs = set(con.execute("""
        SELECT DISTINCT b.token, b.geonameid
        FROM books b JOIN corpus c ON b.dhlabid = c.dhlabid
        WHERE c.category LIKE 'Diktning%'
    """).fetchall())
    con.close()
    return pairs


def main():
    provider     = sys.argv[1] if len(sys.argv) > 1 else "haiku"
    fiction_only = len(sys.argv) > 2 and sys.argv[2] == "fiction"

    if provider == "haiku":
        from anthropic import Anthropic
        client = Anthropic()
        call   = lambda prompt, _: call_haiku(client, prompt)
        model  = ANTHROPIC_MODEL
    elif provider == "q8":
        call  = lambda prompt, _: call_q8(prompt)
        model = Q8_MODEL
    else:
        print(f"Ukjent provider: {provider}. Bruk 'haiku' eller 'q8'.")
        sys.exit(1)

    con      = sqlite3.connect(DISAMBIG_DB, timeout=30)
    con_imag = sqlite3.connect(IMAGINATION_DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(PRIORS_SCHEMA)

    # Hent alle token_types med bokmeta fra rep_dhlabid
    rows = con.execute("""
        SELECT t.surface, t.geonames_id, t.rep_dhlabid, t.category, t.year
        FROM token_types t
        WHERE NOT EXISTS (
            SELECT 1 FROM priors p
            WHERE p.surface = t.surface AND p.geonames_id = t.geonames_id
        )
        ORDER BY t.n_books DESC
    """).fetchall()

    # Hent tittel + forfatter fra imagination.db
    meta = {r[0]: (r[1], r[2]) for r in con_imag.execute(
        "SELECT dhlabid, title, author FROM corpus"
    ).fetchall()}
    con_imag.close()

    if fiction_only:
        fiction_pairs = load_fiction_pairs()
        rows = [(s, g, d, cat, yr) for s, g, d, cat, yr in rows
                if (s, g) in fiction_pairs]

    subset_label = "fiction" if fiction_only else "alle kategorier"
    print(f"Prior-disambiguering: {len(rows):,} token_types ({subset_label}) med {model}")

    errors = 0
    t_start = time.time()

    for i, (surface, geonames_id, rep_dhlabid, category, year) in enumerate(rows, 1):
        title, author = meta.get(rep_dhlabid, (None, None)) if rep_dhlabid else (None, None)
        candidates    = get_candidates_local(normalize_token(surface))
        prompt        = build_prompt(surface, title, author, category, year, candidates)

        try:
            answer, elapsed = call(prompt, None)
        except Exception as e:
            print(f"  [{i:6d}/{len(rows)}] {surface!r:28s} FEIL: {e}")
            answer  = {"label": None, "geonames_id": None, "confidence": None}
            elapsed = 0.0
            errors += 1

        con.execute("""
            INSERT OR REPLACE INTO priors
                (surface, geonames_id, label, pred_geonames_id, confidence, model, elapsed_s)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            surface, geonames_id,
            answer.get("label"),
            answer.get("geonames_id"),
            answer.get("confidence"),
            model,
            round(elapsed, 2),
        ))

        if i % 100 == 0:
            con.commit()
            rate    = i / (time.time() - t_start)
            eta_min = (len(rows) - i) / rate / 60
            print(f"  [{i:6d}/{len(rows)}] {errors} feil, "
                  f"{rate:.1f}/s, ETA {eta_min:.0f} min", flush=True)

        label = str(answer.get("label") or "?")
        pred  = answer.get("geonames_id")
        print(f"  [{i:6d}/{len(rows)}] {surface!r:28s} → {label:6s} "
              f"id={str(pred):10} ({elapsed:.1f}s)")

    con.commit()
    con.close()

    elapsed_total = (time.time() - t_start) / 60
    print(f"\nFerdig: {len(rows):,} priors, {errors} feil, {elapsed_total:.0f} min totalt")


if __name__ == "__main__":
    main()
