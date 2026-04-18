"""
Cascade- og jury-evaluering av geo-disambiguering.

Idé: ulike modeller har ulike styrker og svakheter. I stedet for å kjøre én modell
på alt, kan vi bruke en billig/rask modell som første pass og en sterkere modell
kun der det er usikkerhet.

Observerte propensiteter (fra 500-sample eval):
  nano  : 98% PLACE — høy recall, lav presisjon på PERSON/OTHER → god som Stage 1
  Haiku : 90% PLACE — bedre PERSON/OTHER-disk. → god som verifier/Stage 2
  Qwen  : 89% PLACE — ligner Haiku, gratis men treg
  Gemma3: ukjent    — måles her

Bruk:
  python eval_cascade.py nano+haiku     → cascade: nano Stage1, Haiku Stage2
  python eval_cascade.py nano+q8        → cascade: nano Stage1, Q8 Stage2
  python eval_cascade.py nano+gemma3    → cascade: nano Stage1, Gemma3 Stage2
  python eval_cascade.py jury3          → jury: nano+Haiku+Q8/Gemma3, flertall
  python eval_cascade.py compare        → les eksisterende jsonl og sammenlign

Cascade-logikk (nano+verifier):
  1. Kjør Stage-1-modell (nano) på alle 500.
  2. Send til Stage 2 (verifier) kun de der:
       a. confidence < CONF_THRESHOLD  (usikker)
       b. label=PLACE men kontekst antyder person (heuristikk)
       c. ingen kandidater funnet (geonames_id=null)
  3. Verifier-prompt: vis Stage-1-svaret + be om bekreftelse eller overstyring.
  4. Sluttresultat: Stage-1-svar der vi er sikre, Stage-2-svar ellers.

Jury-logikk (3 modeller):
  - Flertall (2 av 3) avgjør label.
  - Ved uenighet om geonames_id blant PLACE-svar: velg høyest confidence.
"""

import json
import sys
import time
from pathlib import Path

from concordance import build_llm_input
from disambig import (
    build_user_prompt,
    call_anthropic, call_openai_model, call_q8, call_gemma3,
    SYSTEM_PROMPT, NANO_MODEL, ANTHROPIC_MODEL, Q8_BASE_URL, GEMMA3_BASE_URL,
    FEW_SHOT,
)

INPUT           = Path("sample_500_kwic.jsonl")
CONF_THRESHOLD  = 0.80   # under denne sendes til Stage 2
NULL_ID_VERIFY  = True   # verifiser også PLACE-svar uten geonames_id


# ---------------------------------------------------------------------------
# Verifier-prompt: Stage 2 ser Stage-1-svaret og avgjør
# ---------------------------------------------------------------------------

VERIFIER_SYSTEM = """\
Du er en geografisk disambiguerer for norsk 1800-tallstekst.
En annen modell har allerede gitt et forslag. Din oppgave er å avgjøre om
forslaget er korrekt, eller om du vil endre det.

Returner kun gyldig JSON:
{
  "label": "PLACE" | "PERSON" | "OTHER",
  "geonames_id": <heltall eller null>,
  "confidence": <0.0–1.0>,
  "overridden": <true|false>,
  "reasoning": "<kort begrunnelse på norsk>"
}

Regler:
- Sett "overridden": true dersom du endrer label eller geonames_id fra forslaget.
- Sett "overridden": false dersom du er enig med forslaget.
- Samme JSON-regler som ellers: label PLACE kun om faktisk geografisk sted.
"""


def build_verifier_prompt(user_prompt: str, stage1: dict) -> str:
    """Legg Stage-1-svaret øverst i Stage-2-prompten."""
    proposal = (
        f'\nStage-1-forslag:\n'
        f'  label      : {stage1.get("label")}\n'
        f'  geonames_id: {stage1.get("geonames_id")}\n'
        f'  confidence : {stage1.get("confidence")}\n'
        f'  reasoning  : {stage1.get("reasoning", "—")}\n'
        f'\nOppgave: bekreft eller overrid forslaget basert på konteksten nedenfor.\n'
        f'---\n'
    )
    return proposal + user_prompt


# ---------------------------------------------------------------------------
# Needs-verification heuristikk
# ---------------------------------------------------------------------------

def needs_verification(stage1: dict) -> bool:
    """Returnerer True om Stage-2 bør involveres."""
    if stage1.get("confidence") is None:
        return True
    if stage1["confidence"] < CONF_THRESHOLD:
        return True
    if stage1.get("label") == "PLACE" and NULL_ID_VERIFY and stage1.get("geonames_id") is None:
        return True
    return False


# ---------------------------------------------------------------------------
# Cascade runner
# ---------------------------------------------------------------------------

def run_cascade(stage1_fn, stage2_fn, label1: str, label2: str, out_path: Path):
    records = [json.loads(l) for l in INPUT.read_text(encoding="utf-8").splitlines()]
    print(f"Cascade {label1} → {label2}  |  {len(records)} forekomster  |  threshold={CONF_THRESHOLD}")

    results   = []
    verified  = 0
    overridden = 0

    for i, rec in enumerate(records, 1):
        llm_input   = build_llm_input(rec)
        user_prompt = build_user_prompt(llm_input)

        # Stage 1
        try:
            s1, t1 = stage1_fn(user_prompt)
        except Exception as e:
            print(f"  [{i:3d}] Stage1 FEIL: {e}")
            s1 = {"label": None, "geonames_id": None, "confidence": None}
            t1 = 0.0

        final   = s1
        t2      = 0.0
        stage   = 1

        if needs_verification(s1):
            verified += 1
            vp = build_verifier_prompt(user_prompt, s1)
            try:
                s2, t2 = stage2_fn(vp)
                if s2.get("overridden"):
                    overridden += 1
                final = s2
                stage = 2
            except Exception as e:
                print(f"  [{i:3d}] Stage2 FEIL: {e}")

        true  = rec.get("geonameid")
        pred  = final.get("geonames_id", "?")
        match = "✓" if pred == true else "✗"
        flag  = f"[S{stage}{'!' if stage == 2 and final.get('overridden') else ''}]"
        print(f"  [{i:3d}/{len(records)}] {rec['token']!r:28s} → "
              f"{str(final.get('label') or '?'):6s} id={str(pred):10} "
              f"fasit={str(true):10} {match} {flag} "
              f"({t1:.1f}s+{t2:.1f}s)")

        results.append({
            "dhlabid":      rec["dhlabid"],
            "seq_start":    rec["kwic"][0]["seqStart"] if rec.get("kwic") else None,
            "token_len":    rec["kwic"][0]["len"]      if rec.get("kwic") else None,
            "label":        final.get("label"),
            "geonames_id":  final.get("geonames_id"),
            "confidence":   final.get("confidence"),
            "stage":        stage,
            "overridden":   final.get("overridden", False),
            "reasoning":    final.get("reasoning"),
            "elapsed_s1":   round(t1, 2),
            "elapsed_s2":   round(t2, 2),
            "model_s1":     label1,
            "model_s2":     label2 if stage == 2 else None,
        })

    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results),
        encoding="utf-8",
    )

    places   = [r for r in results if r["label"] == "PLACE"]
    rec_idx  = {r["dhlabid"]: r for r in [json.loads(l) for l in INPUT.read_text().splitlines()]}
    correct  = [r for r in places if r["geonames_id"] == rec_idx.get(r["dhlabid"], {}).get("geonameid")]
    s2_calls = verified
    s2_over  = overridden

    print(f"\n--- Resultater ---")
    print(f"PLACE       : {len(places)}/{len(results)} ({100*len(places)//len(results)}%)")
    print(f"ID-treff    : {len(correct)}/{len(results)} ({100*len(correct)//len(results)}%)")
    print(f"Stage-2 kall: {s2_calls}/{len(results)} ({100*s2_calls//len(results)}%)")
    print(f"Overstyrt   : {s2_over}/{s2_calls if s2_calls else 1} ({100*s2_over//(s2_calls or 1)}%)")
    print(f"Output      : {out_path}")


# ---------------------------------------------------------------------------
# Jury runner (3 modeller, flertall)
# ---------------------------------------------------------------------------

def run_jury(model_fns: list[tuple[str, callable]], out_path: Path):
    records = [json.loads(l) for l in INPUT.read_text(encoding="utf-8").splitlines()]
    names   = [n for n, _ in model_fns]
    print(f"Jury {' + '.join(names)}  |  {len(records)} forekomster")

    results = []

    for i, rec in enumerate(records, 1):
        llm_input   = build_llm_input(rec)
        user_prompt = build_user_prompt(llm_input)

        votes = []
        for name, fn in model_fns:
            try:
                ans, elapsed = fn(user_prompt)
                ans["_model"]   = name
                ans["_elapsed"] = elapsed
            except Exception as e:
                print(f"  [{i:3d}] {name} FEIL: {e}")
                ans = {"label": None, "geonames_id": None, "confidence": 0.0,
                       "_model": name, "_elapsed": 0.0}
            votes.append(ans)

        # Flertall-label
        from collections import Counter
        label_votes = Counter(v.get("label") for v in votes if v.get("label"))
        majority_label = label_votes.most_common(1)[0][0] if label_votes else None

        # blant de som stemte på vinnerlabelen, velg høyeste confidence
        winners = [v for v in votes if v.get("label") == majority_label]
        best    = max(winners, key=lambda v: v.get("confidence") or 0.0)

        true  = rec.get("geonameid")
        pred  = best.get("geonames_id", "?")
        match = "✓" if pred == true else "✗"
        agree = "✦" if len(set(v.get("label") for v in votes)) == 1 else "△"
        print(f"  [{i:3d}/{len(records)}] {rec['token']!r:28s} → "
              f"{str(majority_label or '?'):6s} id={str(pred):10} "
              f"fasit={str(true):10} {match} {agree} "
              f"votes=[{' '.join(v.get('label','?') for v in votes)}]")

        results.append({
            "dhlabid":       rec["dhlabid"],
            "seq_start":     rec["kwic"][0]["seqStart"] if rec.get("kwic") else None,
            "token_len":     rec["kwic"][0]["len"]      if rec.get("kwic") else None,
            "label":         majority_label,
            "geonames_id":   best.get("geonames_id"),
            "confidence":    best.get("confidence"),
            "unanimous":     len(set(v.get("label") for v in votes)) == 1,
            "votes":         [{"model": v["_model"], "label": v.get("label"),
                               "geonames_id": v.get("geonames_id"),
                               "confidence": v.get("confidence")} for v in votes],
        })

    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results),
        encoding="utf-8",
    )

    rec_idx = {r["dhlabid"]: r for r in [json.loads(l) for l in INPUT.read_text().splitlines()]}
    places  = [r for r in results if r["label"] == "PLACE"]
    correct = [r for r in places if r["geonames_id"] == rec_idx.get(r["dhlabid"], {}).get("geonameid")]
    unani   = [r for r in results if r.get("unanimous")]

    print(f"\n--- Jury-resultater ---")
    print(f"PLACE       : {len(places)}/{len(results)} ({100*len(places)//len(results)}%)")
    print(f"ID-treff    : {len(correct)}/{len(results)} ({100*len(correct)//len(results)}%)")
    print(f"Enstemmige  : {len(unani)}/{len(results)} ({100*len(unani)//len(results)}%)")
    print(f"Output      : {out_path}")


# ---------------------------------------------------------------------------
# Compare — les eksisterende jsonl-filer og print sammenligning
# ---------------------------------------------------------------------------

def compare_results():
    files = {
        "nano"     : Path("results_nano.jsonl"),
        "nano+pp"  : Path("results_nano_post.jsonl"),
        "nano-fs"  : Path("results_nano_fs.jsonl"),
        "haiku"    : Path("results_anthropic.jsonl"),
        "haiku+pp" : Path("results_anthropic_post.jsonl"),
        "mini"     : Path("results_openai.jsonl"),
        "q8"       : Path("results_q8.jsonl"),
        "gemma3"   : Path("results_gemma3.jsonl"),
    }

    true_ids = {
        json.loads(l)["dhlabid"]: json.loads(l).get("geonameid")
        for l in INPUT.read_text(encoding="utf-8").splitlines()
    }

    print(f"{'Model':<16} {'PLACE%':>7} {'PERSON%':>8} {'OTHER%':>7} {'ID-treff%':>10}")
    print("-" * 56)

    for name, path in files.items():
        if not path.exists():
            continue
        rows   = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        n      = len(rows)
        places = sum(1 for r in rows if r.get("label") == "PLACE")
        persons= sum(1 for r in rows if r.get("label") == "PERSON")
        others = sum(1 for r in rows if r.get("label") == "OTHER")
        hits   = sum(1 for r in rows
                     if r.get("label") == "PLACE"
                     and r.get("geonames_id") == true_ids.get(r["dhlabid"]))
        print(f"{name:<16} {100*places//n:>6}%  {100*persons//n:>7}%  "
              f"{100*others//n:>6}%  {100*hits//n:>9}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"

    if mode == "compare":
        compare_results()
        return

    # Bygg Stage-1 og Stage-2 callables
    if mode.startswith("nano+"):
        from openai import OpenAI
        nano_client = OpenAI()
        stage1_fn   = lambda p: call_openai_model(nano_client, p, NANO_MODEL)
        label1      = "nano"
        verifier    = mode.split("+")[1]
    else:
        print(f"Ukjent modus: {mode}")
        print("Bruk: nano+haiku | nano+q8 | nano+gemma3 | jury3 | compare")
        sys.exit(1)

    if verifier == "haiku":
        from anthropic import Anthropic
        ant = Anthropic()
        # Verifier-systemprompten injiseres via VERIFIER_SYSTEM — overstyr global
        import disambig as _d
        _d.SYSTEM_PROMPT = VERIFIER_SYSTEM
        stage2_fn = lambda p: call_anthropic(ant, p)
        label2    = "haiku"
        out       = Path("results_cascade_nano_haiku.jsonl")

    elif verifier == "q8":
        from openai import OpenAI
        q8c = OpenAI(base_url=Q8_BASE_URL, api_key="not-needed")
        import disambig as _d
        _d.SYSTEM_PROMPT = VERIFIER_SYSTEM
        stage2_fn = lambda p: call_q8(q8c, p)
        label2    = "q8"
        out       = Path("results_cascade_nano_q8.jsonl")

    elif verifier == "gemma3":
        import disambig as _d
        _d.SYSTEM_PROMPT = VERIFIER_SYSTEM
        stage2_fn = lambda p: call_gemma3(p, base_url=GEMMA3_BASE_URL)
        label2    = "gemma3"
        out       = Path("results_cascade_nano_gemma3.jsonl")

    elif mode == "jury3":
        from openai import OpenAI
        from anthropic import Anthropic
        nano_c = OpenAI()
        ant    = Anthropic()
        q8c    = OpenAI(base_url=Q8_BASE_URL, api_key="not-needed")
        model_fns = [
            ("nano",  lambda p: call_openai_model(nano_c, p, NANO_MODEL)),
            ("haiku", lambda p: call_anthropic(ant, p)),
            ("q8",    lambda p: call_q8(q8c, p)),
        ]
        run_jury(model_fns, Path("results_jury3.jsonl"))
        return

    else:
        print(f"Ukjent verifier: {verifier}")
        sys.exit(1)

    run_cascade(stage1_fn, stage2_fn, label1, label2, out)


if __name__ == "__main__":
    main()
