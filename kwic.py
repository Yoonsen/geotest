"""
Steg 2: Hent KWIC for hver forekomst i sample_500.jsonl.

Endepunkt velges basert på antall ord i token:
  Enkeltord:  or_query       termGroups: [["Bergen"]]
  Flerord:    near_fragments termGroups: [["New"], ["York"]], matchMode: "sequence"

renderMode: "structured" gir ren JSON uten inline-markering:
  {"rows": [{"bookId", "seqStart", "len", "before", "hit", "after", "surface"}]}

Output: sample_500_kwic.jsonl
"""

import json
import time
import requests
from pathlib import Path

BASE_URL   = "https://api.nb.no/dhlab/imag"
EP_SINGLE  = f"{BASE_URL}/or_query"
EP_MULTI   = f"{BASE_URL}/near_fragments"

INPUT  = Path("sample_500.jsonl")
OUTPUT = Path("sample_500_kwic.jsonl")

BEFORE      = 15
AFTER       = 15
PER_BOOK    = 3
DOC_SAMPLES = 50
TOTAL_LIMIT = 200
WINDOW      = 3

RETRY_ATTEMPTS = 3
RETRY_BACKOFF  = 2.0


def build_request(token: str, dhlabid: int) -> tuple[str, dict]:
    """Bygg (endpoint, payload) basert på antall ord i token."""
    words = token.split()
    term_groups = [[w] for w in words]

    common = {
        "useFilter":   True,
        "filterIds":   [dhlabid],
        "before":      BEFORE,
        "after":       AFTER,
        "perBook":     PER_BOOK,
        "docSamples":  DOC_SAMPLES,
        "totalLimit":  TOTAL_LIMIT,
        "schema":      "unigrams",
        "renderMode":  "structured",
        "maxVariants": 10,
    }

    if len(words) == 1:
        return EP_SINGLE, {"termGroups": term_groups, **common}
    else:
        return EP_MULTI, {
            "termGroups":  term_groups,
            "matchMode":   "sequence",
            "window":      WINDOW,
            "symmetric":   False,
            "excludeSelf": False,
            "engine":      "python",
            **common,
        }


def fetch_kwic(token: str, dhlabid: int) -> list[dict]:
    """
    Hent strukturerte KWIC-rader for token i én bok.
    Returnerer liste av {bookId, seqStart, len, before, hit, after, surface}.
    """
    endpoint, payload = build_request(token, dhlabid)

    wait = RETRY_BACKOFF
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(endpoint, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json().get("rows", [])
        except Exception as e:
            if attempt == RETRY_ATTEMPTS:
                print(f"    FEIL etter {attempt} forsøk ({endpoint}): {e}")
                return []
            print(f"    Forsøk {attempt} feilet ({e}), venter {wait:.0f}s...")
            time.sleep(wait)
            wait *= 2


def main():
    records = [json.loads(l) for l in INPUT.read_text(encoding="utf-8").splitlines()]
    print(f"Henter KWIC for {len(records)} forekomster...")

    results = []
    for i, rec in enumerate(records, 1):
        token   = rec["token"]
        dhlabid = rec["dhlabid"]

        t0      = time.time()
        rows    = fetch_kwic(token, dhlabid)
        elapsed = time.time() - t0

        rec["kwic"]       = rows   # {seqStart, len, before, hit, after, surface}
        rec["kwic_count"] = len(rows)
        results.append(rec)

        kind   = f"flerord({len(token.split())})" if len(token.split()) > 1 else "enkeltord"
        status = f"{len(rows)} treff" if rows else "ingen treff"
        print(f"  [{i:3d}/500] {token!r:30s} {kind:15s} {status:15s} ({elapsed:.1f}s)")

    OUTPUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results),
        encoding="utf-8"
    )
    print(f"\nFerdig: {OUTPUT} ({len(results)} rader)")
    no_kwic = sum(1 for r in results if not r["kwic"])
    print(f"Uten KWIC: {no_kwic}")


if __name__ == "__main__":
    main()
