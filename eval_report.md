# Evalueringsrapport — geo-disambiguering

Evaluator: claude-sonnet-4-6
Sample: 500 annotasjoner (stratifisert på sjanger)
Dato: 2026-04-15 / 2026-04-16

---

## Om fasiten

Fasiten (`_true_geonameid`) er hentet fra en tidligere BERT-kjøring og brukes kun som
**identifikator** — selve GeoNames-ID-en. Øvrige metadata (navn, feature_class, country_code
osv.) i fasit-databasen skal ikke stoles på og må regenereres fra GeoNames-kilden.
Evalueringen avdekket en del fasit-feil (se eget avsnitt).

---

## Hovedresultater

| | Haiku | Haiku+pp | gpt-5-mini | mini+pp | Q8 | Q8+pp | nano | nano+pp |
|---|---|---|---|---|---|---|---|---|
| PLACE-andel | 449/500 (90%) | | 465/500 (93%) | | 447/500 (89%) | | 492/500 (98%) | |
| PERSON/OTHER | 50/500 (10%) | | 35/500 (7%) | | 53/500 (11%) | | 8/500 (2%) | |
| ID-treff | 233/500 (47%) | 237/500 (47%) | 238/500 (48%) | 239/500 (48%) | 226/500 (45%) | 228/500 (46%) | 228/500 (45%) | 248/500 (49%) |
| Median responstid | 2.5s | | 8.1s | | ~8s | | 1.5s | |
| Total tid (500 kall) | 22 min | | 82 min | | ~67 min | | 14 min | |
| Feil/crashes | 1 | | 0 | | 0 | | 1 | |
| Kostnad (500 kall) | ~$0.18 | | ~$0.50 | | **$0** | | ~$0.03 | |

`+pp` = etter postprosessering (A→P normalisering, se eget avsnitt)  
`Q8` = Qwen3.5-27B Q8_0 på dhlab1 RTX A6000, servert via llama-cpp-python  
`nano` = gpt-4.1-nano (**NB:** eval kjørt mot gpt-4.1-nano; nå finnes gpt-5-nano og gpt-5.4-nano — bør re-evalueres)

**Konklusjon:** Q8 er 2–3 prosentpoeng under de kommersielle modellene, men gratis.
For 110k produksjonskall: ~$0 vs ~$35 (Haiku). Kvalitetsforskjellen er liten nok til at
Q8 er et seriøst alternativ, særlig om man kjører flere iterasjoner.

---

## Divergerende tilfeller (62 av 500)

Haiku og gpt-5-mini er uenige i 62 tilfeller. Fordeling:

| | Antall |
|---|---|
| Haiku riktig, mini feil | 13 |
| mini riktig, Haiku feil | 18 |
| Begge feil, ulike svar | 31 |
| Begge riktige, ulike svar | 0 |

### Haiku vinner (13) — modigere ved usikkerhet

Haiku velger et svar der mini returnerer `null`. Mini er for forsiktig ved tvetydige
kandidatlister. Eksempler der Haiku har rett:

- **Lampertheim** — Haiku: Lampertheim (P/DE) ✓ | mini: null ✗
- **Pierre** — Haiku: Pierre (P/US) ✓ | mini: null ✗ (mini tolker "Pierre" som fornavn)
- **Hannover** — Haiku: Hannover (P/DE) ✓ | mini: Region Hannover (A/DE) ✗
- **Slesvig** — Haiku: Schleswig (P/DE) ✓ | mini: Sønderjylland (L/DK) ✗
- **Americas** — Haiku: America (L/) ✓ | mini: null ✗

### mini vinner (18) — mer presis på feature_class

mini velger P (by) konsekvent der Haiku feilaktig velger A (administrativ enhet).
Dette er **det samme variant-problemet** som postprosesseringen adresserer. Eksempler:

- **Wien** — mini: Vienna (P/AT) ✓ | Haiku: Wien (A/AT) ✗
- **Weimar** — mini: Weimar (P/DE) ✓ | Haiku: Kreisfreie Stadt Weimar (A/DE) ✗
- **Ålesund** (×2) — mini: Ålesund (P/NO) ✓ | Haiku: A/NO eller null ✗
- **Fredrikshald** — mini: Halden (P/NO) ✓ | Haiku: Halden (A/NO) ✗
- **Skibotten** — mini: Ivgobahta (P/NO) ✓ | Haiku: null ✗

mini er også bedre på norske gårds-/soknenavn (Moland, Seim, Tingvoll).

### Begge feil — ulike svar (31)

Tre underkategorier:

**Fasit-feil (~8 tilfeller):**
- **Bandak** — begge velger norsk innsjø (riktig kontekstuelt), fasit sier Afghanistan
- **Langeland** — begge velger norsk gård i Akershus (kontekst: Ryghs Gaardnavne), fasit sier Danmark
- **Toledo** — begge identifiserer Strada di Toledo i Napoli riktig, fasit sier Belize

**OCR-korrupt tekst (~5 tilfeller):**
- **Svartdal**, **Vaaler** — teksten er uleselig, begge modeller gjetter
- Haiku sier `null` (ærlig usikkerhet), mini gjetter likevel

**Ekte vanskeligheter (~18 tilfeller):**
- Svært lokale norske gårdsnavn med mange nær-identiske IDer (Sandbrekka, Furuheim, Frogner)
- Stedsnavn som opptrer i indeks/register-tekst uten tilstrekkelig kontekst
- Flertydige token (Langfjorden, Prestebakke) der riktig ID er blant de minst fremtredende kandidatene

---

## Postprosessering — A→P normalisering

**Regel:** Hvis modellen returnerte en A (administrativ enhet), og det finnes en P (by/tettsted)
i kandidatlisten innenfor 50 km med samme land, velg P i stedet.

| | Endret | Netto gevinst |
|---|---|---|
| Haiku | 21 rader | +4 |
| gpt-5-mini | 12 rader | +1 |

Gevinsten er liten fordi mange A→P-byttene treffer fasit-feil (der A faktisk var riktigere).
Postprosesseringen er likevel riktig for produksjon: byer i 1800-tallstekst bør normaliseres
til P-entiteter, ikke administrative grenser.

---

## Fasit-støy

Avdekkede fasit-feil i `_true_geonameid`:

| Token | Modell velger | Fasit sier | Kommentar |
|---|---|---|---|
| Eidsvold | Eidsvoll, NO (riktig) | Eidsvold, Queensland AU | Norsk koloninavn i Australia |
| ADVERTISEMENT | — | ukjent ID | Ikke et stedsnavn (forlagskolofon) |
| Kontorers | — | — | Genitiv av "kontor", OCR-støy |
| Bandak | norsk innsjø (riktig) | Afghanistan | Feil land |
| Toledo | Napoli-gate (riktig) | Belize | Feil kontinent |
| Langeland | norsk gård (riktig) | Danmark | Feil land |

Fasiten bør behandles som veiledende, ikke autoritativ. Reell treffsikkerhet er høyere
enn de rapporterte 47-48%.

---

## Kandidatoppslag — forbedringer gjort

Opprinnelig søkte `concordance.py` kun i `alternates.alternatename`. Oppdatert til
UNION med `places.name` + `places.asciiname`, og `MAX_CANDIDATES` økt fra 5 til 15.

Effekt for kjente problemtilfeller:

| Token | Før | Etter |
|---|---|---|
| Amerika | USA ikke i kandidatlisten | USA øverst (327M pop) |
| Sahara | Afrikansk ørken på plass 13+ | Med i kandidatlisten (pos 13/15) |
| Haag | The Hague øverst | The Hague øverst ✓ |

---

## Estimert kostnad for 90 000 steder

Ca. 327 tokens inn + 40 tokens ut per kall (eval-modus med reasoning).
I produksjon (uten reasoning): ~327 inn + 15 ut.

**Observert kostnad fra piloten:**

| Skala | Haiku | nano |
|---|---|---|
| 500 kall (pilot) | $0.18 | ~$0.03 |
| 110 000 kall | ~$40 | ~$7 |

Ekstrapolert fra faktisk pilotforbruk — ikke teoretisk modellpris.
**nano** er raskest (1.5s/kall, 14 min for 500) og billigst (~6x billigere enn Haiku),
med litt høyere ID-treff etter postprosessering (49% vs 47%).
Svakheten er at nano nesten alltid returnerer PLACE (98%) — lav presisjon på OTHER/PERSON.

---

## Anbefalinger for produksjon

1. **Modell:** gpt-4.1-nano for volum (6x billigere enn Haiku, litt bedre ID-treff +pp), Haiku der presisjon på OTHER/PERSON er viktig (nano klassifiserer 98% som PLACE)
2. **EVAL_MODE = False** i `disambig.py` — dropp reasoning for å spare ~15% input-tokens
3. **Postprosessering A→P** kjøres etter disambiguering
4. **Kandidatoppslag:** UNION alternates + places.name, 15 kandidater
5. **Tittel/forfatter** fra corpus-tabellen inkluderes i prompt (krever ny sample.py-kjøring)
6. **Fasit:** bruk kun geonames_id som identifikator — regenerer øvrig metadata fra GeoNames
7. **Pre-filtrering:** fjern åpenbar OCR-støy og kolofoner før disambiguering
