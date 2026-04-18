# geotest — LLM-based geo-disambiguation of Norwegian 19th-century texts

Disambiguates place name mentions in the Norwegian National Library's digitized book corpus (Nasjonalbiblioteket) using LLMs and GeoNames. The output is a standoff annotation layer that maps each place mention `(dhlabid, seq_start)` to a canonical GeoNames ID with coordinates and metadata.

## Overview

Norwegian 19th-century literature is full of place names — cities, villages, mountains, fjords — in historical spellings that don't map cleanly to modern gazetteers. This project:

1. Extracts candidate place names and their KWIC concordances from the NB API
2. Looks up GeoNames candidates (up to 15 per surface form)
3. Sends surface + context + candidates to an LLM for disambiguation
4. Stores predictions in a standoff layer, linked to token coordinates `(dhlabid, seq_start)`

The annotation layer feeds directly into [Dash Imagination](https://github.com/Yoonsen/Dash_Imagination), a map-based exploration tool for the corpus.

## Architecture

```
geo_disambig.db
  ├── token_types    — unique (surface, geonames_id) pairs (~110k)
  ├── concordances   — KWIC context from NB API (subsumed flag for overlapping spans)
  ├── predictions    — LLM output per token_type (KWIC-based)
  └── priors         — LLM output per token_type (title-based, no concordance)

annotations.db / annotations_fiction.json
  └── annotations    — standoff: (dhlabid, seq_start, token_len, geonames_id, lat, lon, ...)

imagination_v2.db   (in Dash_Imagination)
  └── geo_annotations — grouped per (dhlabid, geonames_id) for map visualization
```

**Coordinate system:** `(dhlabid, seq_start)` — same token stream as the NB sqlite-backend.

## Pipeline

```
build_db.py init           → create geo_disambig.db schema
build_db.py kwic_batch N   → fetch KWIC concordances from NB API (N token_types at a time)
build_db.py mark_subsumed  → flag shorter spans subsumed by longer at same position
                             (e.g. "Rio" subsumed by "Rio de Janeiro")

disambig_prod.py haiku fiction   → KWIC-based disambiguation via Claude Haiku
prior_prod.py haiku fiction      → title-based prior (no concordance, fallback)

export_annotations.py fiction    → export to annotations_fiction.jsonl
build_annotations_db.py fiction  → build annotations.db with GeoNames metadata
build_imagination_v2.py          → build imagination_v2.db with geo_annotations table
```

## Model evaluation (500-sample, fiction)

| Model | ID-treff | ID-treff +pp | PLACE% | Cost/500 | Speed |
|---|---|---|---|---|---|
| Claude Haiku | 47% | 47% | 90% | ~$0.18 | 2.5s/call |
| gpt-4.1-mini | 48% | 48% | 93% | ~$0.50 | 8.1s/call |
| Qwen3.5-27B Q8 | 45% | 46% | 89% | $0 | ~8s/call |
| gpt-4.1-nano | 45% | **49%** | 98% | ~$0.03 | 1.5s/call |

`+pp` = after A→P postprocessing (administrative units normalized to populated places).  
Q8 = local inference on dhlab1 RTX A6000 via llama-cpp-python.

**Production choice:** Claude Haiku for fiction (best precision on PERSON/OTHER), gpt-4.1-nano as cost-effective alternative (~$7 for 110k calls vs ~$40 for Haiku).

## Key scripts

| Script | Purpose |
|---|---|
| `build_db.py` | DB init, KWIC batch fetch, subsumption marking |
| `disambig_prod.py` | Production disambiguation (KWIC-based) |
| `prior_prod.py` | Title-based prior, local Mac (Haiku) |
| `prior_dhlab1.py` | Title-based prior for Q8 on dhlab1 |
| `export_annotations.py` | Export predictions to JSONL (deduplicates by longest span) |
| `build_annotations_db.py` | Build annotations.db with GeoNames metadata |
| `build_imagination_v2.py` | Build imagination_v2.db with geo_annotations for map view |
| `concordance.py` | GeoNames candidate lookup (UNION alternates + places.name, 15 candidates) |
| `postprocess.py` | A→P normalization (administrative → populated place within 50 km) |
| `disambig.py` | 500-sample test harness (Haiku / mini / Q8 / nano / nano-fs) |
| `evaluate.py` | Evaluation against BERT-based ground truth |
| `sample.py` | Stratified sampling |

## Results (fiction corpus)

- **8 975 predictions** — KWIC-based, Claude Haiku
- **13 843 priors** — title-based, Claude Haiku (fallback for token_types without concordance)
- **268 205 annotations** — standoff positions in 16 430 books, 2 518 unique places
- **27 157 geo_annotation rows** — grouped per (dhlabid, geonames_id), 3 218 books, 2 344 places

## Setup

```bash
# Clone
git clone https://github.com/Yoonsen/geotest.git
cd geotest

# Python env
python -m venv .venv && source .venv/bin/activate
pip install anthropic openai requests tqdm

# GeoNames local DB (~13M places) — build from allCountries.txt + alternateNamesV2.txt
python build_geonames_db.py

# API keys
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
```

## Data sources

- **NB corpus** — Nasjonalbiblioteket digitized books, accessed via [dhlab API](https://api.nb.no/dhlab/)
- **GeoNames** — [geonames.org](https://www.geonames.org/) dump (allCountries + alternateNamesV2)
- **Ground truth** — BERT-based predictions from an earlier NB project, used as approximate reference only

## Notes on subsumption

When KWIC is fetched for "Rio" and "Rio de Janeiro", both generate concordances at the same `(dhlabid, seq_start)`. The `mark_subsumed` step flags the shorter span as subsumed so it is excluded from disambiguation and annotation export. This prevents double-counting and ensures each text position maps to exactly one annotation.

## TEI interop

The standoff coordinates `(dhlabid, seq_start, seq_end)` are designed to be linearizable to TEI standoff spans. Token coordinates are the canonical internal anchor; TEI export/import converts between TEI character offsets and token positions.
