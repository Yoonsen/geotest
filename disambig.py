"""
Steg 4: LLM-disambiguering av stedsnavn.

Bruk:
  python disambig.py openai      → results_openai.jsonl       (gpt-5-mini)
  python disambig.py anthropic   → results_anthropic.jsonl    (claude-haiku-4-5)
  python disambig.py nano        → results_gpt5nano.jsonl      (gpt-5-nano)
  python disambig.py nano2       → results_gpt54nano.jsonl     (gpt-5.4-nano, nyeste)
  python disambig.py nano-fs     → results_gpt5nano_fs.jsonl   (gpt-5-nano + few-shot)
  python disambig.py nano2-fs    → results_gpt54nano_fs.jsonl  (gpt-5.4-nano + few-shot)
  python disambig.py q8          → results_q8.jsonl           (Qwen3.5-27B Q8)
  python disambig.py gemma3      → results_gemma3.jsonl       (Gemma 3 27B)
  python disambig.py gemma3-fs   → results_gemma3_fs.jsonl    (Gemma 3 + few-shot)

Output er minimalt: kun ny/resolved data, ikke ekko av input.
Join mot sample_500_kwic.jsonl på dhlabid for full kontekst.
"""

import json
import sys
import time
from pathlib import Path
from concordance import build_llm_input

INPUT = Path("sample_500_kwic.jsonl")

OPENAI_MODEL    = "gpt-5-mini"
NANO_MODEL      = "gpt-5-nano"        # gpt-5-nano-2025-08-07
NANO2_MODEL     = "gpt-5.4-nano"      # gpt-5.4-nano-2026-03-17 (nyeste)
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
Q8_BASE_URL     = "http://dhlab1.nb.no:9090/v1"
Q8_MODEL        = "qwen3.5-27b-q8"
GEMMA3_BASE_URL = "http://dhlab1.nb.no:9091/v1"   # egen server-port for Gemma 3
GEMMA3_MODEL    = "gemma-3-27b"

EVAL_MODE = True   # True = inkluder reasoning i output (for evaluering); False = prod

# /no_think deaktiverer reasoning-modus for Qwen3.5 (unngår <think>-blokker)
SYSTEM_PROMPT_EVAL = """\
/no_think
Du er en geografisk disambiguerer for norsk 1800-tallstekst.
Avgjør om kandidatordet er et stedsnavn i konteksten, og hvilket GeoNames-sted det refererer til.

Returner kun gyldig JSON:
{
  "label": "PLACE" | "PERSON" | "OTHER",
  "geonames_id": <heltall eller null>,
  "confidence": <0.0–1.0>,
  "reasoning": "<kort begrunnelse på norsk>"
}

Regler:
- label "PLACE" kun om det faktisk er et geografisk sted i konteksten
- geonames_id skal matche én av kandidatene om mulig, ellers null
- confidence 1.0 = helt sikker, 0.0 = rent gjett
"""

SYSTEM_PROMPT_PROD = """\
/no_think
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

SYSTEM_PROMPT = SYSTEM_PROMPT_EVAL if EVAL_MODE else SYSTEM_PROMPT_PROD

# Few-shot eksempler for nano — viser forskjellen mellom PLACE, PERSON og OTHER
# for tvetydige tokens som ofte forveksles
FEW_SHOT = """
Eksempler på korrekt disambiguering:

---
Kandidatord: "Vinje"
Kontekst: ...Aasmund Olafsen [Vinje] . Naar man ser paa de grunde...
GeoNames-kandidater: 8532259 | Vinje | A/ADM2 | NO | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.97, "reasoning": "Vinje er her etternavnet til dikteren A.O. Vinje, ikke stedet"}

---
Kandidatord: "Vinje"
Kontekst: ...Bakkestøjl , O . H . , [Vinje] , Telemarken , for gode dreiede...
GeoNames-kandidater: 8532259 | Vinje | A/ADM2 | NO | ...
→ {"label": "PLACE", "geonames_id": 8532259, "confidence": 0.95, "reasoning": "Vinje brukes her som stedsnavn i Telemark, ikke som personnavn"}

---
Kandidatord: "Sofia"
Kontekst: ...blev gift med [Sofia] Brokkenhus , en Datter af Hendrik...
GeoNames-kandidater: 727011 | Sofia | P/PPLC | BG | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.98, "reasoning": "Sofia er her et kvinnenavn, ikke den bulgarske hovedstaden"}

---
Kandidatord: "Sofia"
Kontekst: ...tog toget fra Wien til [Sofia] og videre til Konstantinopel...
GeoNames-kandidater: 727011 | Sofia | P/PPLC | BG | ...
→ {"label": "PLACE", "geonames_id": 727011, "confidence": 0.99, "reasoning": "Sofia er her bulgarias hovedstad, plassert mellom Wien og Konstantinopel"}

---
Kandidatord: "Soult"
Kontekst: ...Efterat Marskalk [Soult] havde afsendt sin Depeche til Keiseren...
GeoNames-kandidater: 2973997 | Soult | P/PPL | FR | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.99, "reasoning": "Soult er marskalken Nicolas Soult, ikke en fransk landsby"}

---
Kandidatord: "Moss"
Kontekst: ...[Moss] , Edw . L. : Preliminary Notice on the Structure...
GeoNames-kandidater: 3145375 | Moss | P/PPLA | NO | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.95, "reasoning": "Moss er her etternavnet til en forfatter (Edw. L. Moss), ikke byen Moss"}

---
Kandidatord: "Moss"
Kontekst: ...dampskibet ankom til [Moss] tidlig om morgenen , og vi gik straks i land...
GeoNames-kandidater: 3145375 | Moss | P/PPLA | NO | ...
→ {"label": "PLACE", "geonames_id": 3145375, "confidence": 0.99, "reasoning": "Moss er her byen ved Oslofjorden"}

---
Kandidatord: "Cap"
Kontekst: ...dette Emne er bleven drøftet i [Cap] . XXIII , Vol . II...
GeoNames-kandidater: 2960698 | Cap | P/PPL | FR | ...
→ {"label": "OTHER", "geonames_id": null, "confidence": 0.97, "reasoning": "Cap er her en forkortelse for kapittel (Caput/Chapter), ikke et stedsnavn"}

---
Kandidatord: "Christian"
Kontekst: ...Kong [Christian] den Fjerde lod opføre dette slot...
GeoNames-kandidater: 3340206 | Christian | P/PPL | ... | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.99, "reasoning": "Christian er et kongenavn, ikke et sted"}

---
Kandidatord: "Malvik"
Kontekst: ...Anders [Malvik] bor i Malvik og arbeider som fisker...
GeoNames-kandidater: 3145329 | Malvik | A/ADM2 | NO | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.95, "reasoning": "Malvik er her etternavnet til Anders, ikke kommunen"}

---
Kandidatord: "Malvik"
Kontekst: ...Anders Malvik bor i [Malvik] og arbeider som fisker...
GeoNames-kandidater: 3145329 | Malvik | A/ADM2 | NO | ...
→ {"label": "PLACE", "geonames_id": 3145329, "confidence": 0.97, "reasoning": "Malvik er her stedsnavnet etter preposisjonen 'i'"}

---
Kandidatord: "Brenna"
Kontekst: ...ein kunde [Brenna] seg ei Halvtunna kvart Fjordungaar . Per...
GeoNames-kandidater: 7531847 | Brenna | P/PPL | NO | ...
→ {"label": "OTHER", "geonames_id": null, "confidence": 0.92, "reasoning": "Brenna er her et verb (å brenne seg), ikke et stedsnavn"}

---
Kandidatord: "Dan"
Kontekst: ...Det britiske verdensriges [Dan] nelse . Kbhn . 1893...
GeoNames-kandidater: 12641098 | Dan | P/PPL | ... | ...
→ {"label": "OTHER", "geonames_id": null, "confidence": 0.98, "reasoning": "Dan er her slutten av et avkuttet ord (dannelse), ikke et stedsnavn"}

---
Kandidatord: "Tilly"
Kontekst: ...han den efterretning , at [Tilly] den 9 de marts havde erobret...
GeoNames-kandidater: 2785432 | Tilly | P/PPL | FR | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.97, "reasoning": "Tilly er den tyske feltherren Johann Tserclaes von Tilly, ikke en fransk landsby"}

---
Kandidatord: "Fjeld"
Kontekst: ...ender med et meget høit og steilt [Fjeld] , paa den sydøstre Side...
GeoNames-kandidater: 3157394 | Fjeld | P/PPL | NO | ...
→ {"label": "PLACE", "geonames_id": 3157394, "confidence": 0.85, "reasoning": "Fjeld brukes her som et konkret geografisk sted/terrengform"}

---
Kandidatord: "Skaane"
Kontekst: ...vil du ikke [Skaane] den for disses Skyld ? Herren svarede...
GeoNames-kandidater: 3139383 | Skåne | A/ADM1 | SE | ...
→ {"label": "OTHER", "geonames_id": null, "confidence": 0.96, "reasoning": "Skaane er her et verb (å skåne/spare), ikke den svenske landsdelen"}

---
Kandidatord: "Castlereagh"
Kontekst: ...as 'Old Rapid' and Lord [Castlereagh] , son of the statesman , sitting back...
GeoNames-kandidater: 2653558 | Castlereagh | A/ADM2 | GB | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.97, "reasoning": "Castlereagh er her en britisk adelstittel/person, ikke en administrativ enhet i Storbritannia"}

---
Kandidatord: "Ihlen"
Kontekst: ...løitnant Meyer , Nils [Ihlen] , jernverkseier Jacob Neumann...
GeoNames-kandidater: 8588293 | Ihlen | P/PPL | NO | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.97, "reasoning": "Ihlen er her et etternavn i en liste over personer"}

---
Kandidatord: "Solberg"
Kontekst: ...[Solberg] , H . A . , Christiania , for meget gode Portrætter...
GeoNames-kandidater: 777602 | Solberg | P/PPL | NO | ...
→ {"label": "PERSON", "geonames_id": null, "confidence": 0.97, "reasoning": "Solberg er her etternavnet til en fotograf/kunstner fra Christiania"}

---
Kandidatord: "Solberg"
Kontekst: ...kjørte vi gjennom [Solberg] og videre nordover mot Eidsvoll...
GeoNames-kandidater: 777602 | Solberg | P/PPL | NO | ...
→ {"label": "PLACE", "geonames_id": 777602, "confidence": 0.93, "reasoning": "Solberg er her et sted man kjører gjennom, tydelig stedsnavn"}

---
Nå disambiguer du følgende:
"""


def build_user_prompt(llm_input: dict) -> str:
    token = llm_input["token"]
    meta  = llm_input["metadata"]
    concs = llm_input["concordances"]
    cands = llm_input["candidates"]

    lines = []
    if meta.get("title"):
        lines.append(f'Tittel: {meta["title"]}')
    if meta.get("author"):
        lines.append(f'Forfatter: {meta["author"]}')
    lines += [
        f'Sjanger: {meta["category"]}',
        f'År: {meta["year"]}',
        f'Oversatt: {"ja" if meta["translated"] else "nei"}',
        f'Kandidatord: "{token}"',
        "",
        "Kontekst:",
    ]
    for c in concs:
        lines.append(f'  ...{c["before"]} [{c["hit"]}] {c["after"]}...')

    lines.append("")
    if cands:
        lines.append("GeoNames-kandidater:")
        for c in cands:
            lines.append(
                f'  {c["geonames_id"]} | {c["name"]} | '
                f'{c["feature_class"]}/{c["feature_code"]} | '
                f'{c["country_code"]} | {c["lat"]:.4f},{c["lon"]:.4f}'
            )
    else:
        lines.append("GeoNames-kandidater: ingen funnet")

    return "\n".join(lines)


def call_openai_model(client, user_prompt: str, model: str) -> tuple[dict, float]:
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        temperature=1.0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=256,
    )
    return json.loads(resp.choices[0].message.content), time.time() - t0


def call_openai(client, user_prompt: str) -> tuple[dict, float]:
    t0 = time.time()
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=1.0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content), time.time() - t0


def call_anthropic(client, user_prompt: str) -> tuple[dict, float]:
    t0 = time.time()
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    # strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}") + 1
    return json.loads(text[start:end]), time.time() - t0


def call_gemma3(user_prompt: str, base_url: str = GEMMA3_BASE_URL,
                few_shot_prefix: str = "") -> tuple[dict, float]:
    """Kall mot lokal Gemma 3 via raw completions-endepunkt.
    Gemma 3 bruker <start_of_turn>/<end_of_turn>-format (ikke im_start/im_end).
    Ingen /no_think-direktiv — Gemma 3 er ikke et reasoning-model.
    """
    import requests as _req
    prompt_text = few_shot_prefix + user_prompt if few_shot_prefix else user_prompt
    raw = (
        f"<start_of_turn>user\n{SYSTEM_PROMPT}\n\n{prompt_text}<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )
    t0 = time.time()
    resp = _req.post(
        base_url.rstrip("/") + "/completions",
        json={
            "prompt":      raw,
            "max_tokens":  256,
            "temperature": 0.0,
            "stop":        ["<end_of_turn>"],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["text"].strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3].rstrip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1:
        raise ValueError(f"Ingen JSON i svar: {text[:120]}")
    return json.loads(text[start:end]), time.time() - t0


def call_q8(client, user_prompt: str) -> tuple[dict, float]:
    """Kall mot lokal Qwen3.5 Q8 via raw completions-endepunkt.
    Bruker tom <think></think>-blokk som prefiks for å hoppe over reasoning.
    Resulterer i ~7s per kall mot ~25s med full thinking.
    """
    import requests as _req
    raw = (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n</think>\n"
    )
    t0 = time.time()
    resp = _req.post(
        Q8_BASE_URL.rstrip("/") + "/completions",
        json={
            "prompt":      raw,
            "max_tokens":  256,
            "temperature": 0.0,
            "stop":        ["<|im_end|>"],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["text"].strip()
    # Strip markdown code fences
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3].rstrip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1:
        raise ValueError(f"Ingen JSON i svar: {text[:120]}")
    return json.loads(text[start:end]), time.time() - t0


def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "openai"

    if provider == "nano":
        from openai import OpenAI
        client = OpenAI()
        call   = lambda prompt: call_openai_model(client, prompt, NANO_MODEL)
        model  = NANO_MODEL
        output = Path("results_gpt5nano.jsonl")
    elif provider == "nano2":
        from openai import OpenAI
        client = OpenAI()
        call   = lambda prompt: call_openai_model(client, prompt, NANO2_MODEL)
        model  = NANO2_MODEL
        output = Path("results_gpt54nano.jsonl")
    elif provider == "nano-fs":
        from openai import OpenAI
        client = OpenAI()
        call   = lambda prompt: call_openai_model(client, FEW_SHOT + prompt, NANO_MODEL)
        model  = NANO_MODEL + "-fewshot"
        output = Path("results_gpt5nano_fs.jsonl")
    elif provider == "nano2-fs":
        from openai import OpenAI
        client = OpenAI()
        call   = lambda prompt: call_openai_model(client, FEW_SHOT + prompt, NANO2_MODEL)
        model  = NANO2_MODEL + "-fewshot"
        output = Path("results_gpt54nano_fs.jsonl")
    elif provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic()
        call   = lambda prompt: call_anthropic(client, prompt)
        model  = ANTHROPIC_MODEL
        output = Path("results_anthropic.jsonl")
    elif provider == "q8":
        from openai import OpenAI
        client = OpenAI(base_url=Q8_BASE_URL, api_key="not-needed")
        call   = lambda prompt: call_q8(client, prompt)
        model  = Q8_MODEL
        output = Path("results_q8.jsonl")
    elif provider == "gemma3":
        call   = lambda prompt: call_gemma3(prompt)
        model  = GEMMA3_MODEL
        output = Path("results_gemma3.jsonl")
    elif provider == "gemma3-fs":
        call   = lambda prompt: call_gemma3(prompt, few_shot_prefix=FEW_SHOT)
        model  = GEMMA3_MODEL + "-fewshot"
        output = Path("results_gemma3_fs.jsonl")
    else:
        from openai import OpenAI
        client = OpenAI()
        call   = lambda prompt: call_openai(client, prompt)
        model  = OPENAI_MODEL
        output = Path("results_openai.jsonl")

    records = [json.loads(l) for l in INPUT.read_text(encoding="utf-8").splitlines()]
    print(f"Disambiguerer {len(records)} forekomster med {model} → {output}")

    results, errors = [], 0

    for i, rec in enumerate(records, 1):
        llm_input   = build_llm_input(rec)
        user_prompt = build_user_prompt(llm_input)

        try:
            answer, elapsed = call(user_prompt)
        except Exception as e:
            print(f"  [{i:3d}/{len(records)}] FEIL: {e}")
            answer, elapsed = {"label": None, "geonames_id": None, "confidence": None}, 0.0
            errors += 1

        # kun ny/resolved data — join mot input på dhlabid for resten
        result = {
            "dhlabid":        rec["dhlabid"],
            "seq_start":      rec["kwic"][0]["seqStart"] if rec.get("kwic") else None,
            "token_len":      rec["kwic"][0]["len"]      if rec.get("kwic") else None,
            "label":          answer.get("label"),
            "geonames_id":    answer.get("geonames_id"),
            "confidence":     answer.get("confidence"),
            "elapsed_s":      round(elapsed, 2),
            "model":          model,
        }
        if EVAL_MODE and answer.get("reasoning"):
            result["reasoning"] = answer["reasoning"]
        results.append(result)

        pred  = answer.get("geonames_id", "?")
        true  = rec.get("geonameid")
        match = "✓" if pred == true else "✗"
        label = str(answer.get("label") or "?")
        print(f"  [{i:3d}/{len(records)}] {rec['token']!r:28s} → {label:6s} "
              f"id={str(pred):10} fasit={str(true):10} {match} ({elapsed:.1f}s)")

    output.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results),
        encoding="utf-8"
    )
    rec_by_id = {r["dhlabid"]: r for r in records}
    places    = [r for r in results if r["label"] == "PLACE"]
    correct   = [r for r in places if r["geonames_id"] == rec_by_id.get(r["dhlabid"], {}).get("geonameid")]
    print(f"\nFerdig: {output}  |  Feil: {errors}")
    print(f"PLACE: {len(places)}/{len(results)}  |  ID-treff: {len(correct)}/{len(places)}")


if __name__ == "__main__":
    main()
