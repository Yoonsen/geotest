"""
Bygger geo_disambig.db med tre tabeller:

  token_types  — én rad per unik (surface, geonames_id) — LLM-enheten
  concordances — én representativ konkordans per token_type (KWIC fra NB API)
  predictions  — LLM-output per token_type

Dataflyt:
  python build_db.py init [fiction]   — last token_types fra imagination.db
  python build_db.py kwic_batch [N]   — hent KWIC for N overflateformer (ett kall per form,
                                         alle bøker, match tilbake til geonames_id)
  python build_db.py status           — vis fremdrift

Ny KWIC-strategi (krever API med perBook=0/docSamples=0/totalLimit=0-støtte):
  - Ett API-kall per unik overflateform (ingen filterIds)
  - Returnerer alle treff på tvers av alle bøker
  - bookId i responsen matches mot imagination.db for å finne geonames_id
  - Én representativ konkordans lagres per (surface, geonames_id)

LLM-kjøringen (disambig_prod.py) leser token_types + concordances
og skriver til predictions.
"""

import sqlite3
import requests
import time
from pathlib import Path

IMAGINATION_DB = Path("~/Github/Dash_Imagination/src/dash_imagination/data/imagination.db").expanduser()
OUTPUT_DB      = Path("geo_disambig.db")
KWIC_WINDOW    = 25   # ord før/etter (maks støttet av API)

# Topografiske fellesnavn i norsk — stedsformer som kan være proprium ELLER fellesord
# Disse merkes ambiguous=1 og trenger per-forekomst LLM-kall
COMMON_NOUN_PATTERNS = {
    "dal", "vik", "nes", "berg", "bakke", "koll", "haug", "mo", "moen",
    "sand", "strand", "li", "lid", "hei", "mark", "myr", "eng", "voll",
    "bø", "holt", "lund", "ly", "foss", "elv", "å", "bæk", "bekk",
    "fjord", "sund", "bukt", "odde", "øy", "holme", "skjær",
    "skog", "hagen", "garden", "gard", "tun", "bru", "vei",
}


SCHEMA = """
-- Enheten for disambiguering: én rad per unik (overflateform, geonames_id)
CREATE TABLE IF NOT EXISTS token_types (
    surface      TEXT    NOT NULL,
    geonames_id  INTEGER NOT NULL,
    n_books      INTEGER DEFAULT 0,   -- antall bøker der paret forekommer
    rep_dhlabid  INTEGER,             -- representativ bok for KWIC-henting
    category     TEXT,                -- kategori fra representativ bok
    year         INTEGER,             -- år fra representativ bok
    kwic_fetched INTEGER DEFAULT 0,   -- 1 = konkordans hentet
    ambiguous    INTEGER DEFAULT 0,   -- 1 = mulig fellesord, trenger per-forekomst LLM
    PRIMARY KEY (surface, geonames_id)
);

-- Én eller flere konkordanser per (surface, geonames_id)
-- Unambiguous: typisk 1 rad, LLM-prediksjon spres til alle bøker
-- Ambiguous:   én rad per bok, LLM kjøres per forekomst
CREATE TABLE IF NOT EXISTS concordances (
    surface       TEXT    NOT NULL,
    geonames_id   INTEGER NOT NULL,
    dhlabid       INTEGER NOT NULL,
    seq_start     INTEGER NOT NULL,
    token_len     INTEGER NOT NULL,
    before        TEXT,
    after         TEXT,
    PRIMARY KEY (surface, geonames_id, dhlabid, seq_start)
);

-- LLM-prediksjoner per token_type
CREATE TABLE IF NOT EXISTS predictions (
    surface          TEXT    NOT NULL,
    geonames_id      INTEGER NOT NULL,
    label            TEXT,
    pred_geonames_id INTEGER,
    confidence       REAL,
    model            TEXT,
    elapsed_s        REAL,
    PRIMARY KEY (surface, geonames_id)
);

CREATE INDEX IF NOT EXISTS idx_tt_kwic ON token_types(kwic_fetched);
CREATE INDEX IF NOT EXISTS idx_pred_geo ON predictions(pred_geonames_id);
CREATE INDEX IF NOT EXISTS idx_conc_pos ON concordances(dhlabid, seq_start);
"""

SCHEMA_MIGRATE = """
ALTER TABLE concordances ADD COLUMN subsumed INTEGER DEFAULT 0;
"""


def load_token_types(con_out: sqlite3.Connection, fiction_only: bool = False):
    """
    Laster unike (surface, geonames_id)-par fra imagination.db.
    Representativ bok = den med høyest book_count (mest omtale av stedet).
    """
    con_in = sqlite3.connect(IMAGINATION_DB)

    cat_filter = "WHERE c.category LIKE 'Diktning:%'" if fiction_only else ""

    # For hvert (token, geonameid)-par: tell bøker og velg representativ bok
    rows = con_in.execute(f"""
        SELECT
            b.token                                      AS surface,
            b.geonameid                                  AS geonames_id,
            COUNT(DISTINCT b.dhlabid)                    AS n_books,
            MAX(CASE WHEN b.book_count = mx.max_count
                     THEN b.dhlabid END)                 AS rep_dhlabid,
            MAX(CASE WHEN b.book_count = mx.max_count
                     THEN c.category END)                AS category,
            MAX(CASE WHEN b.book_count = mx.max_count
                     THEN c.year END)                    AS year
        FROM books b
        JOIN corpus c ON b.dhlabid = c.dhlabid
        JOIN (
            SELECT token, geonameid, MAX(book_count) AS max_count
            FROM books
            GROUP BY token, geonameid
        ) mx ON b.token = mx.token AND b.geonameid = mx.geonameid
        {cat_filter}
        GROUP BY b.token, b.geonameid
    """).fetchall()
    con_in.close()

    def is_ambiguous(surface: str) -> int:
        """1 hvis overflateformen kan være et topografisk fellesord."""
        low = surface.lower().strip()
        # Sjekk om siste ord (eller hele formen) er et kjent fellesnavn
        last_word = low.split()[-1] if low.split() else low
        return 1 if last_word in COMMON_NOUN_PATTERNS or low in COMMON_NOUN_PATTERNS else 0

    rows_with_flag = [(*r, is_ambiguous(r[0])) for r in rows]

    con_out.executemany("""
        INSERT OR IGNORE INTO token_types
            (surface, geonames_id, n_books, rep_dhlabid, category, year, ambiguous)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows_with_flag)
    con_out.commit()

    n_ambig = sum(1 for r in rows_with_flag if r[-1])
    label = "skjønnlitteratur" if fiction_only else "alle kategorier"
    print(f"Lastet {len(rows):,} unike (token, geonames_id)-par ({label})")
    print(f"  Herav mulige fellesnavn (ambiguous=1): {n_ambig:,}")


BASE_URL  = "https://api.nb.no/dhlab/imag"
EP_SINGLE = f"{BASE_URL}/or_query"
EP_MULTI  = f"{BASE_URL}/near_fragments"


def fetch_kwic(token: str, dhlabid: int) -> list[dict]:
    """Henter konkordanser fra NB API for ett token i én bok."""
    words = token.split()
    term_groups = [[w] for w in words]

    common = {
        "useFilter":   True,
        "filterIds":   [dhlabid],
        "before":      KWIC_WINDOW,
        "after":       KWIC_WINDOW,
        "perBook":     MAX_CONC,
        "docSamples":  50,
        "totalLimit":  10,
        "schema":      "unigrams",
        "renderMode":  "structured",
        "maxVariants": 10,
    }

    if len(words) == 1:
        endpoint = EP_SINGLE
        payload  = {"termGroups": term_groups, **common}
    else:
        endpoint = EP_MULTI
        payload  = {
            "termGroups": term_groups,
            "matchMode":  "sequence",
            "window":     3,
            "symmetric":  False,
            "excludeSelf": False,
            "engine":     "python",
            **common,
        }

    try:
        resp = requests.post(endpoint, json=payload, timeout=30)
        if resp.status_code in (404, 422):
            return []   # No results / invalid token — not an error
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        print(f"    KWIC-feil {token!r} bok {dhlabid}: {e}")
        return []


MAX_FALLBACK_BOOKS = 3   # maks antall bøker å prøve per token_type (gammel strategi)


def fetch_kwic_subkorpus(token: str, dhlabids: list[int], per_book: int = 1) -> list[dict]:
    """
    Henter konkordanser fra et spesifikt subkorpus (liste av bøker).
    Bruker ny API-semantikk: docSamples=0, totalLimit=0.
    per_book=1 for disambiguering (én per bok), per_book=0 for alle forekomster.
    """
    words = token.split()
    term_groups = [[w] for w in words]

    common = {
        "useFilter":   True,
        "filterIds":   dhlabids,
        "before":      KWIC_WINDOW,
        "after":       KWIC_WINDOW,
        "perBook":     per_book,
        "docSamples":  0,
        "totalLimit":  0,
        "schema":      "unigrams",
        "renderMode":  "structured",
    }

    if len(words) == 1:
        endpoint = EP_SINGLE
        payload  = {"termGroups": term_groups, **common}
    else:
        endpoint = EP_MULTI
        payload  = {
            "termGroups":  term_groups,
            "matchMode":   "sequence",
            "window":      3,
            "symmetric":   False,
            "excludeSelf": False,
            "engine":      "python",
            **common,
        }

    try:
        resp = requests.post(endpoint, json=payload, timeout=60)
        if resp.status_code in (404, 422):
            return []
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        print(f"    KWIC-feil {token!r}: {e}")
        return []


def update_kwic_batch(con: sqlite3.Connection, batch_size: int = 500):
    """
    Én API-kall per (surface, geonames_id) med filterIds = alle bøker i subkorpuset.
    Ingen matching etterpå — alle treff tilhører akkurat dette stedet.
    per_book=1: én konkordans per bok (nok for disambiguering).
    """
    pending = con.execute("""
        SELECT surface, geonames_id, ambiguous
        FROM token_types
        WHERE kwic_fetched = 0
        ORDER BY surface
        LIMIT ?
    """, (batch_size,)).fetchall()

    if not pending:
        print("Ingen token_types gjenstår.")
        return

    n_ambig = sum(1 for _, _, a in pending if a)
    print(f"Henter KWIC for {len(pending)} token_types "
          f"({n_ambig} tvetydige → perBook=0, {len(pending)-n_ambig} klare → perBook=1)...")
    fetched = 0

    con_in = sqlite3.connect(IMAGINATION_DB)

    for i, (surface, geonames_id, ambiguous) in enumerate(pending, 1):
        # Subkorpus: alle bøker med akkurat dette (token, geonames_id)
        dhlabids = [r[0] for r in con_in.execute(
            "SELECT dhlabid FROM books WHERE token = ? AND geonameid = ?",
            (surface, geonames_id)
        ).fetchall()]

        if not dhlabids:
            con.execute("UPDATE token_types SET kwic_fetched=1 WHERE surface=? AND geonames_id=?",
                        (surface, geonames_id))
            continue

        # Tvetydige: alle forekomster per bok — klare proprier: én per bok
        per_book = 0 if ambiguous else 1
        rows = fetch_kwic_subkorpus(surface, dhlabids, per_book=per_book)

        for row in rows:
            con.execute("""
                INSERT OR IGNORE INTO concordances
                    (surface, geonames_id, dhlabid, seq_start, token_len, before, after)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                surface, geonames_id,
                row.get("bookId"),
                row["seqStart"],
                row.get("len", 1),
                row.get("before", ""),
                row.get("after", ""),
            ))
        if rows:
            fetched += 1

        con.execute("UPDATE token_types SET kwic_fetched=1 WHERE surface=? AND geonames_id=?",
                    (surface, geonames_id))

        time.sleep(0.05)

        if i % 100 == 0:
            con.commit()
            print(f"  [{i}/{len(pending)}] {fetched} med konkordans...", flush=True)

    con_in.close()
    con.commit()
    print(f"Ferdig: {fetched}/{len(pending)} fikk konkordans")


def update_kwic(con: sqlite3.Connection, batch_size: int = 500):
    """
    Henter KWIC for token_types uten konkordans ennå.
    Prøver inntil MAX_FALLBACK_BOOKS forskjellige bøker per token_type.
    """
    pending = con.execute("""
        SELECT surface, geonames_id, rep_dhlabid
        FROM token_types
        WHERE kwic_fetched = 0 AND rep_dhlabid IS NOT NULL
        LIMIT ?
    """, (batch_size,)).fetchall()

    print(f"Henter KWIC for {len(pending)} token_types...")
    fetched = 0

    # Koble til imagination.db for fallback-bøker
    con_in = sqlite3.connect(IMAGINATION_DB)

    for surface, geonames_id, primary_dhlabid in pending:
        # Bygg prioritert liste: primær bok + fallbacks sortert på book_count DESC
        candidates = con_in.execute("""
            SELECT dhlabid FROM books
            WHERE token = ? AND geonameid = ? AND dhlabid != ?
            ORDER BY book_count DESC
            LIMIT ?
        """, (surface, geonames_id, primary_dhlabid, MAX_FALLBACK_BOOKS - 1)).fetchall()

        book_list = [primary_dhlabid] + [r[0] for r in candidates]
        found = False

        for dhlabid in book_list:
            rows = fetch_kwic(surface, dhlabid)
            if rows:
                row = rows[0]
                con.execute("""
                    INSERT OR REPLACE INTO concordances
                        (surface, geonames_id, rep_dhlabid, rep_seq_start, rep_token_len, before, after)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    surface, geonames_id, dhlabid,
                    row["seqStart"],
                    row.get("len", 1),
                    row.get("before", ""),
                    row.get("after", ""),
                ))
                fetched += 1
                found = True
                break
            time.sleep(0.03)

        con.execute("""
            UPDATE token_types SET kwic_fetched = 1
            WHERE surface = ? AND geonames_id = ?
        """, (surface, geonames_id))

        time.sleep(0.05)

    con_in.close()
    con.commit()
    print(f"Ferdig: {fetched}/{len(pending)} fikk konkordans (resten: ingen treff i noen bok)")


if __name__ == "__main__":
    import sys

    con = sqlite3.connect(OUTPUT_DB, timeout=60)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "init":
        fiction_only = len(sys.argv) > 2 and sys.argv[2] == "fiction"
        load_token_types(con, fiction_only=fiction_only)

    elif cmd == "kwic_batch":
        # Ny strategi: ett kall per overflateform, alle bøker (krever oppdatert API)
        batch = int(sys.argv[2]) if len(sys.argv) > 2 else 500
        update_kwic_batch(con, batch)

    elif cmd == "kwic":
        # Gammel strategi: ett kall per (token_type, bok), med fallbacks
        batch = int(sys.argv[2]) if len(sys.argv) > 2 else 500
        update_kwic(con, batch)

    elif cmd == "mark_subsumed":
        # Legg til subsumed-kolonne om den mangler, og marker subsumt konkordanser
        try:
            con.execute("ALTER TABLE concordances ADD COLUMN subsumed INTEGER DEFAULT 0")
            con.commit()
            print("La til kolonne 'subsumed'.")
        except sqlite3.OperationalError:
            print("Kolonne 'subsumed' finnes allerede.")

        # Marker kortere treff subsumt av lengre treff på samme (dhlabid, seq_start)
        con.execute("""
            UPDATE concordances SET subsumed = 1
            WHERE EXISTS (
                SELECT 1 FROM concordances c2
                WHERE c2.dhlabid   = concordances.dhlabid
                  AND c2.seq_start = concordances.seq_start
                  AND c2.token_len > concordances.token_len
            )
        """)
        n = con.execute("SELECT changes()").fetchone()[0]
        con.commit()
        print(f"Markert {n:,} subsumt konkordanser.")

        total = con.execute("SELECT COUNT(*) FROM concordances").fetchone()[0]
        print(f"Totalt: {total:,} konkordanser, {total - n:,} aktive.")

    elif cmd == "status":
        total   = con.execute("SELECT COUNT(*) FROM token_types").fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM token_types WHERE kwic_fetched=0").fetchone()[0]
        with_conc = con.execute("SELECT COUNT(*) FROM concordances").fetchone()[0]
        done    = con.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        print(f"Token-typer:      {total:,}")
        print(f"  KWIC mangler:   {pending:,}")
        print(f"  Med konkordans: {with_conc:,}")
        print(f"Predictions:      {done:,}")
        if total:
            print(f"  Gjenstår:       {total - done:,}")

    con.close()
