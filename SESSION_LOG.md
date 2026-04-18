# Geotest Session Log

Logg over hva som er gjort, slik at vi kan ta opp tråden etter restart.

---

## 2026-04-17

### Hva som er gjort

#### Produksjonskjøringer fullført

**Fiction KWIC-disambiguering (Haiku):**
- `disambig_prod.py haiku fiction` — ferdig, 8 975 predictions, 61 feil, 454 min, ~$11
- Predictions i `geo_disambig.db` → `predictions`-tabellen

**Fiction prior-disambiguering (Haiku):**
- `prior_prod.py haiku fiction` — kjører, ~9 600/13 843 ferdig (~$12 så langt)
- Tittelbasert (ingen KWIC), fallback for token_types uten konkordans
- Predictions i `geo_disambig.db` → `priors`-tabellen

**Non-fiction prior (Q8, dhlab1):**
- `prior_dhlab1.py` — kjører i screen `qwen_prior`, ~4 000/96 338 ferdig
- Leser fra `prior_nonfiction.jsonl` (pre-generert, 24 MB)
- Skriver til `priors_dhlab1.db` på dhlab1

**KWIC-løkke (dhlab1):**
- 37 161 token_types hadde `kwic_fetched=0` — aldri hentet (batch ble aldri kjørt til ferdig)
- Løkke startet på dhlab1: `screen -S kwic_loop`, henter 5000 om gangen til ferdig
- Logg: `~/geotest/kwic_batch_dhlab1.log`
- Etter ferdig: kjør `disambig_prod.py haiku fiction` på nytt for ~3 500 resterende fiction-par

#### Annotasjonseksport

**`export_annotations.py fiction`:**
- 428 397 annotasjoner (før subsumed-filtrering)
- Fil: `annotations_fiction.jsonl`

**`build_annotations_db.py fiction`:**
- Bygger `annotations.db` med full GeoNames-metadata per `(dhlabid, seq_start)`
- 268 205 rader, 16 430 bøker, 2 518 unike steder
- Eksportert til `annotations_fiction.json` (92 MB)

**`build_imagination_v2.py fiction`:**
- Bygger `imagination_v2.db` = kopi av imagination.db + ny tabell `geo_annotations`
- `geo_annotations`: gruppert per `(dhlabid, geonames_id)` med frekvenstelling og metadata
- 27 157 rader, 3 218 bøker, 2 344 unike steder
- Brukes til kartvisning i appen: hent alle steder i en bok, plot etter frekvens
- Filen: `~/Github/Dash_Imagination/src/dash_imagination/data/imagination_v2.db`

#### Subsumsjonslogikk

Problemet: "Rio" (len=1) og "Rio de Janeiro" (len=3) gir konkordanser på samme `(dhlabid, seq_start)`.
Løsning i to lag:
1. **`build_db.py mark_subsumed`** — legger til `subsumed`-kolonne i `concordances`, markerer kortere treff subsumt av lengre på samme posisjon. Kjøres etter at KWIC-løkken er ferdig (DB låst nå).
2. **Eksport-filteret** — velger lengste `token_len` per `(dhlabid, seq_start)` i Python

#### gpt-4.1-nano test

Kjørt `disambig.py nano` på 500-sample:

| | nano | nano+pp | Haiku+pp | mini+pp | Q8+pp |
|---|---|---|---|---|---|
| PLACE-andel | **98%** | | 90% | 93% | 89% |
| ID-treff | 45% | **49%** | 47% | 48% | 46% |
| Median tid | **1.5s** | | 2.5s | 8.1s | ~8s |
| Kostnad 500 | **~$0.03** | | $0.18 | $0.50 | $0 |

**Svakhet:** nano returnerer nesten alltid PLACE (PERSON=1, OTHER=6 av 500).
26 tilfeller der nano sier PLACE mens alle andre sier PERSON/OTHER.
Eksempler: Vinje (dikteren), Soult (marskalken), Tilly, Castlereagh, Solberg (fotograf).

**Few-shot test:** `disambig.py nano-fs` kjører nå — 20 eksempler i system-prompten
med tvetydige tokens (person vs. sted, forkortelse vs. sted).
Logg: `results_nano_fs.log`. Sammenlign mot `results_nano.jsonl` når ferdig.

**Idé for videre:** POS-tagg / parsing av kontekstvinduet som ekstra signal —
"fornavn foran token" → sannsynlig PERSON, "preposisjon foran" → sannsynlig PLACE.

### Kjøringer som går akkurat nå

| Prosess | Maskin | Status |
|---|---|---|
| `prior_prod.py haiku fiction` | Mac (lokalt) | ~70% ferdig |
| `prior_dhlab1.py` (Q8) | dhlab1 `screen qwen_prior` | ~4% ferdig, ETA ~50t |
| KWIC-løkke | dhlab1 `screen kwic_loop` | kjører, ~29k gjenstår |
| `disambig.py nano-fs` | Mac (lokalt) | kjører, ~500 kall |
| Q8-server | dhlab1 `screen qwen_server` | oppe på port 9090 |

### Neste steg

1. **Når nano-fs er ferdig:** sammenlign label-fordeling mot nano zero-shot, oppdater eval_report.md
2. **Når KWIC-løkke er ferdig (dhlab1):** kjør `build_db.py mark_subsumed` lokalt
3. **Etter mark_subsumed:** kjør `disambig_prod.py haiku fiction` på nytt for ~3 500 resterende fiction-par
4. **Etter all fiction-disambiguering:** rebuild `annotations.db` og `imagination_v2.db`
5. **Non-fiction disambiguering:** vurder nano-fs (billig, $7 for 110k) vs Haiku ($40) vs Q8 (gratis, 8 dager)
6. **merge dhlab1 DB:** når KWIC-løkke på dhlab1 er ferdig, kopier `geo_disambig.db` tilbake til Mac og merge concordances

### Kostnader så langt

- Fiction KWIC (Haiku): ~$11
- Fiction prior (Haiku): ~$12 (ikke ferdig)
- Totalt brukt: ~$23

### Arkitektur — standoff-lag

```
imagination_v2.db
  ├── corpus          — bokmeta (22 946 bøker)
  ├── books           — gammelt BERT-lag (token_type-nivå, fallback)
  ├── places          — gammel stedstabell
  └── geo_annotations — nytt lag: (dhlabid, geonames_id), gruppert med frekvens + metadata

annotations.db / annotations_fiction.json
  └── annotations     — (dhlabid, seq_start, token_len, geonames_id, name, lat, lon, ...)
                        Brukes til å bygge postings/bitmap-laget i sqlite-backend

Koordinatsystem: (dhlabid, seq_start) — samme som tokenstrøm i sqlite-backend
Bitmap-struktur: per geonames_id → post_start (alle seq_start), post_len1/2/3 (for rendering)
```

### Filer

```
geotest/
├── geo_disambig.db           # hoveddatabase: token_types, concordances, predictions, priors
├── geonames.db               # lokal GeoNames (13M steder + 18M alternates)
├── annotations.db            # annotasjoner med GeoNames-metadata
├── annotations_fiction.json  # eksport til annotasjonslag (92 MB)
├── prior_nonfiction.jsonl    # pre-generert liste for Q8-prior på dhlab1 (24 MB)
├── build_db.py               # init/kwic_batch/mark_subsumed/status
├── disambig_prod.py          # KWIC-basert prod-disambiguering (haiku/q8)
├── prior_prod.py             # titttelbasert prior (haiku/q8, lokalt)
├── prior_dhlab1.py           # titttelbasert prior for Q8 på dhlab1
├── export_annotations.py     # eksporter predictions til JSONL
├── build_annotations_db.py   # bygg annotations.db med GeoNames-metadata
├── build_imagination_v2.py   # bygg imagination_v2.db med geo_annotations
├── postprocess.py            # A→P normalisering
├── disambig.py               # 500-sample test (openai/anthropic/q8/nano/nano-fs)
├── eval_report.md            # evalueringsrapport (oppdatert med nano)
├── results_nano.jsonl        # nano zero-shot resultater
├── results_nano_fs.jsonl     # nano few-shot resultater (under kjøring)
└── SESSION_LOG.md            # denne filen
```

---

## 2026-04-16

### Resultater — Haiku vs gpt-5-mini vs Q8 (500 sample)

Alle tre kjøringer fullført. Postprosessering (A→P) kjørt på alle.

| | Haiku | Haiku+pp | gpt-5-mini | mini+pp | Q8 | Q8+pp |
|---|---|---|---|---|---|---|
| ID-treff | 233 (47%) | 237 (47%) | 238 (48%) | 239 (48%) | 226 (45%) | 228 (46%) |
| PLACE-andel | 90% | | 93% | | 89% | |
| Median responstid | 2.5s | | 8.1s | | ~8s | |
| Total tid (500 kall) | 22 min | | 82 min | | ~67 min | |
| Kostnad (500 kall) | ~$0.18 | | ~$0.50 | | $0 | |

`Q8` = Qwen3.5-27B Q8_0, dhlab1 RTX A6000

Filer: `results_anthropic.jsonl`, `results_openai.jsonl`, `results_q8.jsonl` (+ `_post`-varianter)

### Produksjonsdatabase — geo_disambig.db

Ny arkitektur: `(surface, geonames_id)` som enhet, ikke `(dhlabid, token)`.

| Tabell | Innhold |
|---|---|
| `token_types` | 110 181 unike (overflateform, geonames_id)-par |
| `concordances` | KWIC fra NB API, subkorpus-strategi |
| `predictions` | LLM-output per token_type |

### NB API — ny batch-semantikk

- `perBook=0` → alle treff per bok
- `docSamples=0` → ingen dokumentsampling
- `totalLimit=0` → ingen totalgrense
- Maks vindu: 25 ord
- Corpus-wide søk: utelat `filterIds` + `useFilter=False`

### Q8-oppsett på dhlab1

- Modell: `/mnt/disk3/models/Qwen3.5-27B.Q8_0.gguf` (27 GB)
- GPU: RTX A6000 (49 GB VRAM)
- Server: `llama-cpp-python` 0.3.20, port 9090
- Trick: tom `<think></think>`-prefiks via raw `/v1/completions` (~8s vs ~25s)

### API-nøkler

- `OPENAI_API_KEY` — satt i miljøet
- `ANTHROPIC_API_KEY`:
  ```bash
  export ANTHROPIC_API_KEY=$(ssh dhlab1.nb.no "grep ANTHROPIC_API_KEY ~/.bashrc | cut -d= -f2")
  ```

---

## 2026-04-15

### Prosjektstatus

- Prosjekt: `~/Github/geotest/` — LLM-basert geo-disambiguering av stedsnavn i norske 1800-tallsbøker
- Geodatabase: `~/Github/geo_loc_disambig/geo_norsk.db`
- Kildedatabase: `~/Github/Dash_Imagination/src/dash_imagination/data/imagination.db`

### Evalueringsresultat (manuell, 20 sample)

- 44% korrekt, 17% variant, 28% feil, 10% fasit-støy
- Feilene skyldes primært dårlige GeoNames-kandidater, ikke modellen
