# Innkjøpsvurdering: Apple M5 Mac mini til lokal LLM-inferens

**Prosjekt:** Geo-disambiguering av stedsnavn i NB-korpuset  
**Dato:** April 2026  
**Kontekst:** Nasjonalbiblioteket digitaliserte samling — LLM-basert annotasjonspipeline

---

## Bakgrunn

Prosjektet disambiguerer stedsnavn i norske 1800-tallsbøker ved hjelp av LLM-er og
GeoNames. Hvert stedsnavn disambigueres mot opptil 15 GeoNames-kandidater basert på KWIC-konkordanser og bokmeta (tittel, forfatter, sjanger).

Pipeline-en er under produksjon for skjønnlitteratur (~22 946 bøker, ~110 000 unike stedsnavn-forekomster) og skal skaleres til hele korpuset. I tillegg pågår eksperimentering
med kaskade- og jury-evaluering der flere modeller kombineres.

---

## Skalaestimat

Oppgaven er i to lag:

| Lag | Beskrivelse | Estimert volum |
|---|---|---|
| **Disambiguering** | Én LLM-kall per unikt (surface, geonames_id)-par | ~110 000 kall nå; ~500 000 ved full skala |
| **Full tekst / re-annotasjon** | Inferens over løpende tekst | 1,5 mrd tokens input, ~500 mill tokens output |

Full-tekst-skalaen er relevant for fremtidige oppgaver: entitetsgjenkjenning, relasjonsekstraksjon, TEI-standoff-generering over hele NB-korpuset.

---

## API-kostnader ved full skala

Prisene nedenfor er omtrentlige liseprisnivåer (per april 2026) og bør verifiseres
mot gjeldende prislister. Estimatene forutsetter **1,5 mrd input-tokens** og
**500 mill output-tokens**.

| Modell | Input ($/1M) | Output ($/1M) | Input-kost | Output-kost | **Total** |
|---|---|---|---|---|---|
| Claude Haiku 4.5 | $0,80 | $4,00 | $1 200 | $2 000 | **$3 200** |
| gpt-5.4-nano | ~$0,80 | ~$4,00 | ~$1 200 | ~$2 000 | **~$3 200** |
| gpt-4.1-mini | $0,40 | $1,60 | $600 | $800 | **$1 400** |
| **gpt-5-nano** | **~$0,10** | **~$0,40** | **~$150** | **~$200** | **~$350** |
| gpt-4.1-nano | $0,10 | $0,40 | $150 | $200 | **$350** |
| Claude Sonnet 4.x | $3,00 | $15,00 | $4 500 | $7 500 | **$12 000** |
| Qwen 3.5-27B (lokal) | $0 | $0 | $0 | $0 | **$0** |
| Gemma 3 27B (lokal) | $0 | $0 | $0 | $0 | **$0** |

> **Obs:** `gpt-5.4-nano` er raskere enn Haiku (~1s/kall vs 2.5s) og litt bedre på kvalitet,
> men tilsvarende i pris. `gpt-5-nano` er ~8x raskere enn `gpt-5.4-nano` men ~6x billigere —
> beste kvalitet/pris-forhold av de kommersielle alternativene.
>
> Kaskade-arkitekturen (billig modell Stage 1 → sterkere modell Stage 2 kun for
> usikre tilfeller) kan redusere Stage-2-kall med 60–70 %, noe som halverer API-kostnadene
> for hybridoppsett.

---

## Maskinvarekostnader — Apple M5 Mac mini

Lokal inferens krever nok unified memory til å laste modellen i Q8-kvantisering:

| Modell | Modellstørrelse (Q8) | RAM-krav |
|---|---|---|
| Gemma 3 27B | ~28 GB | min. 32 GB |
| Qwen 3.5 27B | ~28 GB | min. 32 GB |
| To modeller parallelt | ~56 GB | min. 64 GB |

### Anbefalte konfigurasjoner

| Konfigurasjon | Unified RAM | Egnet for | Ca. pris (NOK) |
|---|---|---|---|
| M5 Mac mini (basis) | 16 GB | Ikke tilstrekkelig for 27B Q8 | ~8 000 |
| M5 Mac mini (32 GB) | 32 GB | Én 27B-modell, god margin | ~15 000–18 000 |
| M5 Pro Mac mini (48 GB) | 48 GB | Én 27B + buffer / parallell 7B | ~22 000–26 000 |
| M5 Max Mac mini (64 GB+) | 64 GB | To 27B parallelt, jury-oppsett | ~35 000–45 000 |

> Prisene er estimerte og bør verifiseres mot gjeldende Apple-prisliste / offentlige
> innkjøpsavtaler. M5 Pro med 48 GB er det anbefalte kompromisset.

---

## Inferens-ytelse: M5 vs dhlab1 RTX A6000

| | dhlab1 (RTX A6000) | M5 Pro Mac mini (est.) |
|---|---|---|
| Minne | 49 GB GDDR6 | 48 GB unified |
| Qwen/Gemma 27B Q8 hastighet | ~8s/kall (15–20 tok/s) | ~3–5s/kall (30–50 tok/s, est.) |
| Strømforbruk | ~300 W | ~20–30 W |
| Strøm per år (24/7) | ~2 600 kWh → ~3 000 kr | ~175–260 kWh → ~200–300 kr |
| Tilgjengelighet | Delt ressurs (screen-sessioner) | Dedikert, alltid på |
| Modeller parallelt | Én om gangen | Én om gangen (48 GB) |

> M5 Pro-spennet for tokenhastighet er estimert fra Apple MLX-benchmarks for M4 Pro
> + forventet M5-generasjonsgevinst (~20–30 %). Verifiser mot Ollama/MLX-benchmarks
> når M5 er tilgjengelig.

---

## Gjennomstrømningstid ved full skala

Forutsetter 1,5 mrd input + 500 mill output = **2 mrd tokens totalt**, og
gjennomsnittlig 30 tok/s (M5 Pro) vs 17 tok/s (A6000):

| Maskin | Hastighet | Estimert kjøretid |
|---|---|---|
| dhlab1 A6000 | 17 tok/s | ~1 360 timer (~57 dager) |
| M5 Pro Mac mini | 30 tok/s | ~770 timer (~32 dager) |
| Begge parallelt | 47 tok/s | ~490 timer (~20 dager) |

For det faktiske disambigueringsprosjektet (110 000 kall × ~350 tokens = ~38 mill tokens)
er kjøretidene mye kortere: **dhlab1 ~37 timer, M5 ~21 timer**.

---

## Kaskade-arkitektur med to maskiner

Med M5 Mini + dhlab1 kan vi kjøre kaskade-pipeline med fysisk separasjon:

```
Input (token_type + kontekst)
        │
        ▼
  [M5 Mac mini]
  Stage 1: Gemma 3 27B / Qwen
  Rask, billig, høy recall
        │
        │── confidence > 0.80 → aksepter direkte
        │
        ▼ (confidence ≤ 0.80 eller PERSON-mistanke)
  [dhlab1 RTX A6000]
  Stage 2: annen modell
  Tyngre, kun usikre tilfeller (~20–30 %)
        │
        ▼
  Sluttannotasjon
```

Forventet effekt: Stage 2 trigges for ~20–30 % av tilfellene.
Samlet gjennomstrømning øker, og de to maskinene utfyller hverandre fremfor å konkurrere.

---

## ROI-vurdering

Forutsatt **én full korpuskjøring per år** (1,5 mrd input + 500 mill output):

| Scenario | År 1 | År 2 | År 3 | Total 3 år |
|---|---|---|---|---|
| Claude Haiku (API) | $3 200 | $3 200 | $3 200 | **$9 600** |
| gpt-4.1-nano (API) | $350 | $350 | $350 | **$1 050** |
| M5 Pro 48 GB (lokal) | ~25 000 kr + ~300 kr strøm | ~300 kr | ~300 kr | **~26 000 kr** |
| API-besparelse vs Haiku (3 år) | | | | **~$8 400 (~90 000 kr)** |

> Break-even mot Haiku API nås allerede etter **én** full kjøring om en bruker Haiku-klasse
> modell lokalt. Mot nano er break-even ~8 kjøringer (rimelig for iterativt prosjekt).
>
> Merk: USD/NOK-kurs er ikke fastsatt her — bruk gjeldende kurs ved beregning.

---

## Øvrige fordeler

- **Datakontroll:** tekstkorpuset forlater ikke NB-infrastruktur
- **Ingen API-rategrenser:** fri gjennomstrømning, ingen throttling ved store batch-kjøringer
- **Iterasjonsfreihet:** kan kjøre eksperimentelle modeller (nye Gemma, Mistral, Llama-varianter) uten ekstra kostnad
- **Parallell jury-evaluering:** M5 + dhlab1 kan kjøres som jury (to modeller stemmer, se `eval_cascade.py`)
- **Lav driftsterskel:** Ollama eller llama-cpp-python, ingen serveradministrasjon
- **Lavt strømforbruk:** M5-brikken er ~10–15× mer energieffektiv enn A6000 per token

---

## Anbefaling

**M5 Pro Mac mini med 48 GB unified memory** dekker umiddelbare behov og gir
god margin for fremtidige modeller. 64 GB er ønskelig om budsjettet tillater det
(muliggjør parallell kjøring av to 27B-modeller = jury-oppsett uten dhlab1).

Minimum-krav: **32 GB unified memory** (M5 med 32 GB eller M5 Pro).

---

## Referanser

- Prosjektkode og evalueringsresultater: <https://github.com/Yoonsen/geotest>
- Eval-rapport med modellsammenligning: `eval_report.md`
- Kaskade-pipeline: `eval_cascade.py`
- Benchmarks: [Ollama M4 Pro benchmarks](https://ollama.com) (verifiser for M5 når tilgjengelig)
