"""
Steg 1: Sample 500 forekomster fra ImagiNation geo-data.

Kilder:
  imagination.db  — books (dhlabid, token, geonameid) + corpus (dhlabid, category, year, oversatt)
  geo_norsk.db    — geo (geonameid, name, lat, lon, feature_class, feature_code, country_code)

Alt koordinat- og stadnavndata kommer fra GeoNames via geo_norsk.db.

Output: sample_500.jsonl
"""

import json
import sqlite3
import pandas as pd
from pathlib import Path

IMAGINATION_DB = Path("~/Github/Dash_Imagination/src/dash_imagination/data/imagination.db").expanduser()
GEO_DB = Path("~/Github/geo_loc_disambig/geo_norsk.db").expanduser()
SAMPLE_SIZE = 500
OUTPUT = Path("sample_500.jsonl")


def load_data() -> pd.DataFrame:
    con_imag = sqlite3.connect(IMAGINATION_DB)
    con_geo = sqlite3.connect(GEO_DB)

    books = pd.read_sql("SELECT dhlabid, token, geonameid, feature_class, feature_code FROM books", con_imag)
    corpus = pd.read_sql("SELECT dhlabid, title, author, category, year, oversatt FROM corpus", con_imag)
    geo = pd.read_sql("""
        SELECT geonameid, name, asciiname, latitude, longitude,
               "feature class" AS feature_class_geo,
               "feature code" AS feature_code_geo,
               "country code" AS country_code
        FROM geo
    """, con_geo)

    con_imag.close()
    con_geo.close()

    # join books -> corpus
    df = books.merge(corpus, on="dhlabid", how="inner")

    # join -> geo for fasit-koordinater og stedsnavn
    df = df.merge(geo, on="geonameid", how="left")

    return df


def stratified_sample(df: pd.DataFrame, n: int) -> pd.DataFrame:
    counts = df["category"].value_counts()
    fractions = (counts / counts.sum() * n).round().astype(int)

    diff = n - fractions.sum()
    fractions.iloc[0] += diff

    parts = []
    for category, k in fractions.items():
        subset = df[df["category"] == category]
        k = min(k, len(subset))
        parts.append(subset.sample(n=k, random_state=42))

    return pd.concat(parts).reset_index(drop=True)


def main():
    print("Laster data...")
    df = load_data()
    print(f"  Totalt (dhlabid, token)-par: {len(df):,}")
    print(f"  Med GeoNames-treff: {df['latitude'].notna().sum():,}")
    print(f"  Unike kategorier: {df['category'].nunique()}")

    print(f"\nSampler {SAMPLE_SIZE} stratifisert på kategori...")
    sample = stratified_sample(df, SAMPLE_SIZE)

    print(f"\nFordeling per kategori:")
    for cat, n in sample["category"].value_counts().items():
        print(f"  {cat}: {n}")

    print(f"\nSkriver {OUTPUT}...")
    with open(OUTPUT, "w", encoding="utf-8") as f:
        for _, row in sample.iterrows():
            record = {
                "dhlabid":      int(row["dhlabid"]),
                "token":        row["token"],
                "geonameid":    int(row["geonameid"]) if pd.notna(row["geonameid"]) else None,
                "name":         row["name"] if pd.notna(row["name"]) else None,
                "asciiname":    row["asciiname"] if pd.notna(row["asciiname"]) else None,
                "latitude":     float(row["latitude"]) if pd.notna(row["latitude"]) else None,
                "longitude":    float(row["longitude"]) if pd.notna(row["longitude"]) else None,
                "feature_class": row["feature_class_geo"] if pd.notna(row["feature_class_geo"]) else row["feature_class"],
                "feature_code": row["feature_code_geo"] if pd.notna(row["feature_code_geo"]) else row["feature_code"],
                "country_code": row["country_code"] if pd.notna(row["country_code"]) else None,
                "title":        row["title"] if pd.notna(row["title"]) else None,
                "author":       row["author"] if pd.notna(row["author"]) else None,
                "category":     row["category"],
                "year":         int(row["year"]) if pd.notna(row["year"]) else None,
                "oversatt":     int(row["oversatt"]) if pd.notna(row["oversatt"]) else None,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Ferdig: {OUTPUT} ({sample.shape[0]} rader)")


if __name__ == "__main__":
    main()
