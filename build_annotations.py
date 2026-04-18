"""
Bygg annoteringslag fra disambiguerings-resultater + KWIC.

Én annotasjonsrad per (book_id, seq_start) — klar for import til geo_spans.

Output: annotations.jsonl
"""

import json
from pathlib import Path
from concordance import normalize_token
from places import lookup_place

RESULTS_FILE = Path("results_A.jsonl")
KWIC_FILE    = Path("sample_500_kwic.jsonl")
OUTPUT       = Path("annotations.jsonl")


def main():
    results = {
        (r["dhlabid"], r["token"]): r
        for r in (json.loads(l) for l in RESULTS_FILE.read_text().splitlines())
    }
    kwic_records = [json.loads(l) for l in KWIC_FILE.read_text().splitlines()]

    annotations = []
    skipped = 0

    for rec in kwic_records:
        key = (rec["dhlabid"], rec["token"])
        res = results.get(key)
        if not res:
            skipped += 1
            continue

        # hopp over om ikke klassifisert som PLACE
        if res.get("label") != "PLACE":
            skipped += 1
            continue

        for hit in rec.get("kwic", []):
            if not isinstance(hit, dict) or "seqStart" not in hit:
                continue

            surface   = hit.get("surface") or hit.get("hit") or rec["token"]
            canonical = normalize_token(surface)
            pred_id   = res.get("pred_geonameid")
            label     = res.get("label", "PLACE")

            # fallback: PLACE uten geonames_id → lokal negativ ID
            if label == "PLACE" and not pred_id:
                place        = lookup_place(canonical, None)
                pred_id      = place["geonameid"]   # negativ
                review_state = "unresolved"
            else:
                review_state = "pending"

            annotations.append({
                # koordinater — raw fra konkordansen, aldri modifisert
                "book_id":   hit["bookId"],
                "seq_start": hit["seqStart"],
                "token_len": hit["len"],
                # overflateform — raw fra teksten
                "surface":   surface,
                # kanonisk form — normalisert ("St. Petersburg")
                "canonical": canonical,
                # disambiguering
                "geonames_id":   pred_id,
                "feature_class": res.get("true_feature_class"),
                "country_code":  res.get("true_country_code"),
                "confidence":    res.get("confidence"),
                # sporbarhet
                "reasoning":    res.get("reasoning"),
                "model":        res.get("model"),
                "review_state": review_state,
                # kontekst
                "before": hit.get("before"),
                "after":  hit.get("after"),
                # fasit
                "_true_geonameid": res.get("true_geonameid"),
                "_category":       res.get("category"),
                "_year":           res.get("year"),
            })

    OUTPUT.write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in annotations),
        encoding="utf-8"
    )

    print(f"Annotasjoner: {len(annotations)}")
    print(f"Hoppet over:  {skipped}")
    print(f"Ferdig: {OUTPUT}")

    # liten statistikk
    with_id  = sum(1 for a in annotations if a["geonames_id"])
    correct  = sum(1 for a in annotations if a["geonames_id"] == a["_true_geonameid"])
    print(f"Med geonames_id: {with_id}/{len(annotations)}")
    print(f"Korrekt ID:      {correct}/{with_id} ({100*correct/with_id:.0f}%)" if with_id else "")


if __name__ == "__main__":
    main()
