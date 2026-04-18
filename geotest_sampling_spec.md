# Geotagging 500-test: Sampling og evalueringsoppsett

## Kontekst

Vi har 90 000 eksisterende geoannoterte stedsnavn fra en tidligere BERT-kjøring over
et korpus av norske 1800-tallsbøker. Disse brukes som fasit for å evaluere to
tilnærminger til LLM-basert geo-disambiguering:

1. **KWIC-basert** (±15 ord rundt forekomsten)
2. **Hel bok** (hele bokteksten som kontekst)

Mot to modeller:
- `gpt-5-mini` (online, OpenAI)
- `Qwen2.5-32B Q4` (lokal, vLLM/Ollama på dhlab1)

---

## Steg 1: Sample 500 forekomster fra 90K

Hent 500 tilfeldige forekomster fra den eksisterende geo-annotasjonsdatabasen.

**Krav til sample:**
- Stratifisert på sjanger (bruk prosentvis fordeling fra metadata)
- Inkluder felt: `surface_form`, `book_id`, `token_position`, `geonames_id`, `lat`, `lon`
- Lagre som `sample_500.jsonl`

**Sjangre som skal være representert:**
- Skjønnlitteratur (roman, novelle, lyrikk, dramatikk, barnelitt)
- Religiøse tekster
- Historiske tekster
- Episk diktning
- Øvrige

---

## Steg 2: Hent KWIC for hver forekomst

For hver forekomst i `sample_500.jsonl`:
- Hent ±15 ord rundt `token_position` fra bokteksten
- Lagre som felt `kwic` i `sample_500_kwic.jsonl`

---

## Steg 3: Hent GeoNames-kandidater

For hver `surface_form`:
- Slå opp i lokal SQLite `alternateNames`-tabell
- Hent topp 5 kandidater: `(geonames_id, name, feature_class, feature_code, country_code, lat, lon)`
- Lagre som felt `geonames_candidates` i samme fil

---

## Steg 4: Kjør LLM-disambiguering

### Prompt-template

**System:**
```
Du er en geografisk disambiguerer for norsk 1800-tallstekst.
Returner alltid gyldig JSON, ingenting annet.
```

**User:**
```
Tekst-metadata: {sjanger}, {år}, oversatt: {ja/nei}
Kandidatord: "{surface_form}"
Kontekst: "...{kwic}..."

GeoNames-kandidater:
{geonames_id} | {name} | {feature_class} | {country_code} | {lat} | {lon}
...

Returner:
{
  "label": "PLACE" | "PERSON" | "OTHER",
  "geonames_id": <id eller null>,
  "confidence": 0.0–1.0,
  "reasoning": "<kort begrunnelse på norsk>"
}
```

### Betingelser å kjøre

| Betingelse | Modell | Kontekst |
|---|---|---|
| A | gpt-5-mini | KWIC |
| B | gpt-5-mini | Hel bok |
| C | Qwen2.5-32B | KWIC |

For betingelse B: erstatt `kwic`-feltet med hele bokteksten.

Lagre output som `results_A.jsonl`, `results_B.jsonl`, `results_C.jsonl`.

---

## Steg 5: Evaluering

For hver betingelse, beregn mot fasit (`sample_500.jsonl`):

### Metrikker

1. **Klassifikasjonsaccuracy** — andel korrekte PLACE/PERSON/OTHER
2. **GeoNames-ID accuracy** — andel korrekte `geonames_id` (kun for PLACE)
3. **Koordinatavstand** — median km-avstand fra fasit (kun for PLACE)
4. **Gjennomsnittlig responstid** per kall (sekunder)

### Konfusjonsmatrise

Lag konfusjonsmatrise for PLACE/PERSON/OTHER per betingelse.

### Stratifisert analyse

Bryt ned accuracy per sjanger — skjønnlitteratur er særlig viktig.

### Output

Lagre evalueringsrapport som `eval_report.md` med:
- Sammenligningstabeller A vs B vs C
- Feilanalyse: hvilke former er vanskeligst?
- Estimert kjøretid for 2500 skjønnlitterære bøker per betingelse

---

## Filstruktur

```
geotest/
├── sample_500.jsonl           # raw sample med fasit
├── sample_500_kwic.jsonl      # med KWIC og GeoNames-kandidater
├── results_A.jsonl            # gpt-5-mini + KWIC
├── results_B.jsonl            # gpt-5-mini + hel bok
├── results_C.jsonl            # Qwen + KWIC
└── eval_report.md             # evalueringsrapport
```

---

## Merknader

- Hel bok (betingelse B) kan være tregt — sett et timeout per kall
- Logg alle kall med tidsstempel for ettersporing
- Ved API-feil: retry 3 ganger med eksponentiell backoff
- Qwen kjøres via OpenAI-kompatibelt endepunkt på dhlab1
