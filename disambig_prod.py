"""
Produksjons-disambiguering: leser fra geo_disambig.db, skriver til predictions.

Bruk:
  python disambig_prod.py haiku   [fiction]   → Haiku, alle eller bare fiction
  python disambig_prod.py q8      [fiction]   → Qwen3.5 Q8 på dhlab1
  python disambig_prod.py nano    [fiction]   → gpt-5-nano
  python disambig_prod.py nano2   [fiction]   → gpt-5.4-nano (raskest, anbefalt for volum)

Filtrering:
  fiction  — kun token_types som finnes i minst én Diktning-bok
  (ingen)  — alle token_types

Kjøres mot database som kan ha pågående KWIC-henting — bruker WAL-modus.
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
NANO_MODEL      = "gpt-5-nano"
NANO2_MODEL     = "gpt-5.4-nano"

SYSTEM_PROMPT = """\
Du er en geografisk disambiguerer for norsk 1800-tallstekst.
Avgjør om kandidatordet er et stedsnavn i konteksten, og hvilket GeoNames-sted det refererer til.

Returner kun gyldig JSON:
{
  "label": "PLACE" | "PERSON" | "OTHER",
  "geonames_id": <heltall eller null>,
  "confidence": <0.0–1.0>
}

Regler:
- label "PLACE" kun om det faktisk er et geografisk sted i konteksten
- geonames_id skal matche én av kandidatene om mulig, ellers null
- confidence 1.0 = helt sikker, 0.0 = rent gjett
"""


def build_prompt(surface: str, concs: list[dict], candidates: list[dict],
                 category: str | None, year: int | None) -> str:
    lines = []
    if category:
        lines.append(f"Sjanger: {category}")
    if year:
        lines.append(f"År: {year}")
    lines += [
        f'Kandidatord: "{surface}"',
        "",
        "Kontekst:",
    ]
    for c in concs[:3]:
        before = (c.get("before") or "")[-60:]
        after  = (c.get("after")  or "")[:60]
        lines.append(f"  ...{before} [{surface}] {after}...")

    lines.append("")
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
    provider    = sys.argv[1] if len(sys.argv) > 1 else "haiku"
    fiction_only = len(sys.argv) > 2 and sys.argv[2] == "fiction"

    # Sett opp klient
    if provider == "haiku":
        from anthropic import Anthropic
        client = Anthropic()
        call   = lambda prompt, _: call_haiku(client, prompt)
        model  = ANTHROPIC_MODEL
    elif provider == "q8":
        call  = lambda prompt, _: call_q8(prompt)
        model = Q8_MODEL
    elif provider in ("nano", "nano2"):
        from openai import OpenAI
        oai   = OpenAI()
        model = NANO_MODEL if provider == "nano" else NANO2_MODEL
        def call(prompt, _):
            t0   = time.time()
            resp = oai.chat.completions.create(
                model=model, temperature=1.0,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user",   "content": prompt}],
                response_format={"type": "json_object"},
                max_completion_tokens=128,
            )
            return parse_json(resp.choices[0].message.content), time.time() - t0
    else:
        print(f"Ukjent provider: {provider}. Bruk 'haiku', 'q8', 'nano' eller 'nano2'.")
        sys.exit(1)

    fiction_pairs = load_fiction_pairs() if fiction_only else None
    subset_label  = "fiction" if fiction_only else "alle kategorier"

    con = sqlite3.connect(DISAMBIG_DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")

    # Hent token_types med konkordans, ikke allerede disambiguert
    rows = con.execute("""
        SELECT t.surface, t.geonames_id, t.category, t.year,
               GROUP_CONCAT(c.before  || '|||' || c.after, '^^^')  AS conc_blob
        FROM token_types t
        JOIN concordances c ON t.surface=c.surface AND t.geonames_id=c.geonames_id
        WHERE t.kwic_fetched = 1
          AND (c.subsumed IS NULL OR c.subsumed = 0)
          AND NOT EXISTS (
              SELECT 1 FROM predictions p
              WHERE p.surface=t.surface AND p.geonames_id=t.geonames_id
          )
        GROUP BY t.surface, t.geonames_id
        ORDER BY t.n_books DESC
    """).fetchall()

    if fiction_only:
        rows = [(s, g, cat, yr, blob) for s, g, cat, yr, blob in rows
                if (s, g) in fiction_pairs]

    print(f"Disambiguerer {len(rows):,} token_types ({subset_label}) med {model}")

    done = errors = 0
    t_start = time.time()

    for i, (surface, geonames_id, category, year, conc_blob) in enumerate(rows, 1):
        # Parse konkordanser fra blob
        concs = []
        for entry in (conc_blob or "").split("^^^"):
            parts = entry.split("|||", 1)
            if len(parts) == 2:
                concs.append({"before": parts[0], "after": parts[1]})

        # Kandidater fra lokal GeoNames-DB
        candidates = get_candidates_local(normalize_token(surface))

        prompt = build_prompt(surface, concs, candidates, category, year)

        try:
            answer, elapsed = call(prompt, None)
        except Exception as e:
            print(f"  [{i:5d}/{len(rows)}] {surface!r:28s} FEIL: {e}")
            answer  = {"label": None, "geonames_id": None, "confidence": None}
            elapsed = 0.0
            errors += 1

        con.execute("""
            INSERT OR REPLACE INTO predictions
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

        if i % 50 == 0:
            con.commit()
            rate    = i / (time.time() - t_start)
            eta_min = (len(rows) - i) / rate / 60
            print(f"  [{i:5d}/{len(rows)}] {done} predictions, "
                  f"{rate:.1f}/s, ETA {eta_min:.0f} min", flush=True)

        label = str(answer.get("label") or "?")
        pred  = answer.get("geonames_id")
        print(f"  [{i:5d}/{len(rows)}] {surface!r:28s} → {label:6s} "
              f"id={str(pred):10} ({elapsed:.1f}s)")
        done += 1

    con.commit()
    con.close()

    elapsed_total = (time.time() - t_start) / 60
    print(f"\nFerdig: {done:,} predictions, {errors} feil, {elapsed_total:.0f} min totalt")


if __name__ == "__main__":
    main()
