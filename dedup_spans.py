"""
Fjerner subsumerte spenn fra KWIC-data før disambiguering.

Regel: kast oppføring A hvis det i samme bok finnes oppføring B der
  B.seq_start <= A.seq_start  OG
  B.seq_start + B.len >= A.seq_start + A.len
  (dvs. B dekker hele A eller mer)

Eksempel:
  (book=1005, seq=103, len=1, "Rio")          ← kastes
  (book=1005, seq=103, len=3, "Rio de Janeiro") ← beholdes

Bruk:
  python dedup_spans.py sample_500_kwic.jsonl
  → skriver sample_500_kwic_dedup.jsonl
"""

import json
import sys
from pathlib import Path


def first_seq(record: dict) -> int | None:
    """seq_start for første KWIC-treff, eller None."""
    kwic = record.get("kwic") or []
    for row in kwic:
        if isinstance(row, dict) and "seqStart" in row:
            return row["seqStart"]
    return None


def first_len(record: dict) -> int:
    kwic = record.get("kwic") or []
    for row in kwic:
        if isinstance(row, dict) and "len" in row:
            return row["len"]
    return 1


def dedup(records: list[dict]) -> tuple[list[dict], int]:
    """
    Returnerer (filtrert liste, antall fjernet).
    Grupperer per (dhlabid, seq_start) og beholder lengste span.
    Deretter fjerner også spans som er subsumt av et annet span i samme bok.
    """
    # Bygg opp span-info per bok
    # { dhlabid: [(seq_start, seq_end, record_index), ...] }
    from collections import defaultdict
    book_spans: dict[int, list[tuple[int, int, int]]] = defaultdict(list)

    for i, rec in enumerate(records):
        seq = first_seq(rec)
        if seq is None:
            continue
        length = first_len(rec)
        book_spans[rec["dhlabid"]].append((seq, seq + length - 1, i))

    # For hvert span: finn om det er subsumt av et annet
    subsumed = set()
    for did, spans in book_spans.items():
        for i, (a_start, a_end, a_idx) in enumerate(spans):
            for j, (b_start, b_end, b_idx) in enumerate(spans):
                if i == j:
                    continue
                # B subsumerer A hvis B starter på eller før A og slutter på eller etter A
                if b_start <= a_start and b_end >= a_end and (b_end - b_start) > (a_end - a_start):
                    subsumed.add(a_idx)
                    break

    kept    = [r for i, r in enumerate(records) if i not in subsumed]
    removed = len(subsumed)
    return kept, removed


def main():
    input_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sample_500_kwic.jsonl")
    output_file = input_file.with_stem(input_file.stem + "_dedup")

    records = [json.loads(l) for l in input_file.read_text(encoding="utf-8").splitlines()]
    print(f"Inn: {len(records)} oppføringer")

    kept, removed = dedup(records)
    print(f"Fjernet (subsumt): {removed}")
    print(f"Beholdt: {len(kept)}")

    output_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in kept),
        encoding="utf-8"
    )
    print(f"Skrevet: {output_file}")


if __name__ == "__main__":
    main()
