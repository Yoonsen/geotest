"""
Evaluering av disambiguering med OpenAI.

Tar et stratifisert sample av annotations.jsonl og ber en LLM vurdere
om prediksjonen er korrekt — inkl. å flagge feil i fasiten.

Output: eval_sample.jsonl  +  eval_report.md
"""

import json
import random
import sqlite3
from pathlib import Path
from openai import OpenAI

ANNOTATIONS = Path("annotations.jsonl")
GEO_DB      = Path("geonames.db")
OUTPUT_JSONL = Path("eval_sample.jsonl")
OUTPUT_MD    = Path("eval_report.md")

SAMPLE_SIZE = 50
MODEL       = "gpt-5-mini"


def get_place_name(geonames_id: int | None) -> str:
    if not geonames_id:
        return "ukjent"
    con = sqlite3.connect(GEO_DB)
    row = con.execute(
        "SELECT name, feature_class, country_code FROM places WHERE geonameid=?",
        (geonames_id,)
    ).fetchone()
    con.close()
    return f"{row[0]} ({row[1]}/{row[2]})" if row else f"id={geonames_id}"


def build_eval_prompt(ann: dict) -> str:
    pred_name = get_place_name(ann["geonames_id"])
    true_name = get_place_name(ann["_true_geonameid"])

    return f"""Du evaluerer en automatisk geo-disambiguering av norsk 1800-tallstekst.

Overflateform: "{ann['surface']}"
Kontekst: ...{ann['before']} [{ann['surface']}] {ann['after']}...
Sjanger: {ann.get('_category', '?')}, år: {ann.get('_year', '?')}

Modellens svar:
  label:       PLACE
  geonames_id: {ann['geonames_id']} ({pred_name})
  confidence:  {ann['confidence']}
  reasoning:   {ann['reasoning']}

Fasit (BERT):
  geonames_id: {ann['_true_geonameid']} ({true_name})

Vurder:
1. Er modellens valg korrekt basert på konteksten?
2. Er det samme sted som fasiten, bare ulik ID-variant (admin-nivå)?
3. Er fasiten muligens feil?

Returner JSON:
{{
  "verdict": "correct" | "variant" | "wrong" | "fasit_feil",
  "correct_geonames_id": <id eller null om usikker>,
  "comment": "<kort norsk kommentar>"
}}"""


def main():
    client = OpenAI()
    annotations = [json.loads(l) for l in ANNOTATIONS.read_text().splitlines()]

    # stratifisert sample: ta med feil og riktige, ulike kategorier
    correct = [a for a in annotations if a["geonames_id"] == a["_true_geonameid"]]
    wrong   = [a for a in annotations if a["geonames_id"] != a["_true_geonameid"] and a["geonames_id"]]
    no_id   = [a for a in annotations if not a["geonames_id"]]

    n_correct = min(20, len(correct))
    n_wrong   = min(25, len(wrong))
    n_no_id   = min(5,  len(no_id))

    sample = (
        random.sample(correct, n_correct) +
        random.sample(wrong,   n_wrong) +
        random.sample(no_id,   n_no_id)
    )
    random.shuffle(sample)
    print(f"Sample: {len(sample)} ({n_correct} riktige, {n_wrong} feil, {n_no_id} uten ID)")

    results = []
    verdicts = {"correct": 0, "variant": 0, "wrong": 0, "fasit_feil": 0, "error": 0}

    for i, ann in enumerate(sample, 1):
        prompt = build_eval_prompt(ann)
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content.strip()
            # finn JSON i svaret
            start = text.find("{")
            end   = text.rfind("}") + 1
            eval_result = json.loads(text[start:end])
        except Exception as e:
            eval_result = {"verdict": "error", "comment": str(e)}

        verdict = eval_result.get("verdict", "error")
        verdicts[verdict] = verdicts.get(verdict, 0) + 1

        results.append({**ann, "eval": eval_result})
        print(f"  [{i:2d}/{len(sample)}] {ann['surface']:25s} → {verdict:12s}  {eval_result.get('comment','')[:60]}")

    OUTPUT_JSONL.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results),
        encoding="utf-8"
    )

    # rapport
    total = len(results)
    report = [
        "# Evalueringsrapport — geo-disambiguering",
        "",
        f"Modell evaluert: gpt-5-mini  |  Evaluator: {MODEL}",
        f"Sample: {total} annotasjoner",
        "",
        "## Resultater",
        "",
        f"| Verdict | Antall | Andel |",
        f"|---------|--------|-------|",
    ]
    for v, n in verdicts.items():
        report.append(f"| {v:12s} | {n:6d} | {100*n/total:.0f}% |")

    report += [
        "",
        "## Eksempler per kategori",
        "",
    ]
    for verdict in ["correct", "variant", "fasit_feil", "wrong"]:
        examples = [r for r in results if r["eval"].get("verdict") == verdict][:3]
        if examples:
            report.append(f"### {verdict}")
            for r in examples:
                report.append(f"- **{r['surface']}** ({r.get('_year','?')}): {r['eval'].get('comment','')}")
            report.append("")

    OUTPUT_MD.write_text("\n".join(report), encoding="utf-8")
    print(f"\nFerdig: {OUTPUT_JSONL}, {OUTPUT_MD}")
    print(f"Verdicts: {verdicts}")


if __name__ == "__main__":
    main()
