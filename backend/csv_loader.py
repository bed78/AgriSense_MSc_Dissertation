"""
Batch CSV ingestion pipeline for AgriSense.

Scans a shared network folder for sensor CSV files, cleans and validates
each row, and bulk-inserts new readings into the `agricsensors` hypertable.
Already-processed files are tracked in the `processed_files` table so
re-running this script only ingests genuinely new files.

Run directly with: `python csv_loader.py`
Also invoked automatically by auto_ingest_daemon.py whenever a new CSV
appears in the watched network folder.
"""

import os
import json
import asyncio
import asyncpg
import pandas as pd
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("csv_loader")

# 1. HARDCODED ABSOLUTE PATH
# Root folder that gets recursively scanned for *.csv files.
DATA_ROOT = Path(r"C:\Users\danso\Aberystwyth University\Fred Labrosse [ffl] (Staff) - agriSensors")


def fix_timestamps(ts_series: pd.Series) -> pd.Series:
    """
    Normalize timestamp strings into a format pandas can parse reliably.

    Some source files write timestamps with more than 6 digits of
    fractional-second precision (e.g. nanoseconds), which pandas'
    ISO8601 parser can choke on. This trims any excess digits after the
    first 6 (microseconds) and ensures a proper UTC offset suffix before
    parsing, returning a tz-aware UTC datetime series. Unparseable values
    become NaT (Not a Time) via `errors="coerce"`.
    """
    s = ts_series.astype(str).str.replace(r"(\.\d{6})\d+Z?", r"\1Z", regex=True).str.replace("Z", "+00:00", regex=False)
    return pd.to_datetime(s, format="ISO8601", utc=True, errors="coerce")


async def process_file(pool: asyncpg.Pool, filepath: Path) -> int:
    """
    Load, clean, and insert a single CSV file's readings into the database.

    Steps:
      1. Read the raw CSV (no header row; columns assigned explicitly).
      2. Fix/parse timestamps and drop rows where that fails.
      3. Null out any physically-impossible sensor readings (out-of-range
         values are treated as bad readings, not deleted rows).
      4. Drop rows where every measurement column ended up null.
      5. Bulk-insert the surviving rows, skipping duplicates via
         ON CONFLICT DO NOTHING (sensor_id + date_time is the unique key).
      6. Record the file as processed so it's not re-ingested next run.

    Returns the number of rows inserted (0 on failure or if nothing to insert).
    """
    log.info(f"📄 Load {filepath.name}")
    try:
        # Source files have no header row, so column names are assigned manually.
        df = pd.read_csv(filepath, header=None, names=["sensor_id", "date_time", "light_intensity", "soil_temperature", "watermark_frequency", "watermark"])
        df["date_time"] = fix_timestamps(df["date_time"])
        df = df.dropna(subset=["date_time"])  # unparseable timestamps can't be inserted

        # Range-validate each measurement column; anything outside the
        # physically plausible range is treated as a bad reading and
        # replaced with NULL rather than discarding the whole row.
        df.loc[~df["light_intensity"].between(0, 200000), "light_intensity"] = None
        df.loc[~df["soil_temperature"].between(-10, 60), "soil_temperature"] = None
        df.loc[~df["watermark_frequency"].between(0, 9999), "watermark_frequency"] = None
        df.loc[~df["watermark"].between(0, 300), "watermark"] = None
        # If ALL four measurements failed validation, the row carries no
        # useful information, so drop it entirely.
        df = df.dropna(subset=["light_intensity", "soil_temperature", "watermark_frequency", "watermark"], how="all")

        # Build the list of tuples asyncpg expects for executemany().
        # sensor_id is normalized to lowercase/stripped so it matches
        # consistently across CSV, MQTT, and API sources.
        records = [(str(row.sensor_id).lower().strip(), row.date_time, None if pd.isna(row.light_intensity) else float(row.light_intensity), None if pd.isna(row.soil_temperature) else float(row.soil_temperature), None if pd.isna(row.watermark_frequency) else float(row.watermark_frequency), None if pd.isna(row.watermark) else float(row.watermark)) for _, row in df.iterrows()]

        if not records:
            return 0

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Inserts safely, ignoring duplicate rows automatically
                await conn.executemany("""
                    INSERT INTO agricsensors (sensor_id, date_time, light_intensity, soil_temperature, watermark_frequency, watermark)
                    VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (sensor_id, date_time) DO NOTHING
                """, records)

                # Update the 'processed_at' timestamp so the script knows exactly when this file was last read
                await conn.execute("""
                    INSERT INTO processed_files (filename) VALUES ($1) 
                    ON CONFLICT (filename) DO UPDATE SET processed_at = NOW()
                """, filepath.name)

        log.info(f"✔ Inserted {len(records)} clean rows.")
        return len(records)
    except Exception as e:
        # Any single bad file shouldn't crash the whole batch run.
        log.error(f"✘ Error processing {filepath.name}: {e}")
        return 0


async def main():
    """
    Entry point: connect to the database, find CSV files that haven't
    been processed yet, and ingest all of them concurrently.
    """
    log.info("🚀 Starting Batch CSV Ingestion Pipeline...")

    # Prefer conf.json for DB credentials; fall back to an env var if
    # no conf file is present (e.g. in a containerized deployment).
    conf_path = Path("conf.json")
    if conf_path.exists():
        with open(conf_path) as f: settings = json.load(f)
        DB_URL = f"postgresql://{settings['DBUSER']}:{settings['PASSWORD']}@{settings.get('DBHOST', 'localhost')}:{settings.get('DBPORT', 5433)}/{settings['DBNAME']}"
    else: DB_URL = os.getenv("DATABASE_URL")

    if not DATA_ROOT.exists():
        log.error(f"✘ Could not find the university folder: {DATA_ROOT}")
        return

    pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=10)
    try:
        async with pool.acquire() as conn:
            # 1. Fetch the exact names of EVERY file the database has already processed
            processed_records = await conn.fetch("SELECT filename FROM processed_files")
            processed_history = {r["filename"] for r in processed_records}

        new_files = []

        # 2. Broaden the search pattern to "*.csv" to ensure no older files 
        # are missed due to slight naming convention differences.
        for f in DATA_ROOT.rglob("*.csv"):

            # 3. SMART DIFF: Only queue the file for ingestion if the database 
            # has no memory of ever processing it.
            if f.name not in processed_history:
                new_files.append(f)

        if not new_files:
            log.info("✅ No new CSV files found for ingestion. Database is up to date!")
            return

        log.info(f"🔍 Found {len(new_files)} new files to ingest. Processing...")
        # Process all new files concurrently rather than one at a time.
        results = await asyncio.gather(*[process_file(pool, f) for f in new_files])
        total_rows = sum(results)
        log.info(f"✅ Pipeline Complete! Files Loaded: {len(new_files)} | Total Rows: {total_rows}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
