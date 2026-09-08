"""
Bulk-insert throughput benchmark for the `agricsensors` hypertable.

Generates synthetic sensor readings and times a bulk INSERT so we can
report rows-per-second for the ingestion pipeline. Kept consistent with
csv_loader.py's data rules and Schema.sql's constraints:

  - sensor_id must already exist in `sensor_coordinates` (FK constraint)
    and is lowercased/stripped, matching csv_loader.py's normalization.
  - date_time is timezone-aware (UTC), matching the TIMESTAMPTZ column
    and csv_loader.py's tz-aware parsing.
  - DB port defaults to 5433, matching csv_loader.py's default.

By default the synthetic rows are deleted after the benchmark completes
so this script doesn't permanently pollute the real dataset. Pass
--keep-data to leave them in place.
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

# Distinct prefix for synthetic test rows so they're easy to identify
# and safely clean up afterward without touching real sensor data.
TEST_RUN_TAG = "throughput-test"
SYNTHETIC_START = datetime(2099, 1, 1, tzinfo=timezone.utc)


def load_config():
    try:
        with open("conf.json", "r") as config_file:
            return json.load(config_file)
    except FileNotFoundError:
        print("Error: conf.json not found.")
        sys.exit(1)


def fetch_active_sensor_ids(cursor):
    """
    Pull real sensor_ids from sensor_coordinates so synthetic rows satisfy
    the FK constraint on agricsensors.sensor_id. Normalized the same way
    csv_loader.py normalizes incoming sensor_id values (lower/strip).
    """
    cursor.execute("SELECT sensor_id FROM sensor_coordinates WHERE is_active = TRUE")
    rows = [r[0].strip().lower() for r in cursor.fetchall()]
    if not rows:
        print(
            "Error: no active sensors found in sensor_coordinates. "
            "Insert at least one sensor row before running this benchmark."
        )
        sys.exit(1)
    return rows


def generate_synthetic_payload(num_rows, sensor_ids):
    """Generates a dummy dataset mimicking the hardware nodes."""
    print(f"Generating {num_rows} synthetic rows in memory...")
    data = []

    for i in range(num_rows):
        sensor_id = random.choice(sensor_ids)
        # Use a timestamp range far in the future so synthetic rows can
        # never collide with real sensor readings, and are trivial to
        # identify/clean up afterward.
        date_time = SYNTHETIC_START + timedelta(minutes=i)

        # Validated against the boundaries enforced in csv_loader.py
        light_intensity = round(random.uniform(0, 200000), 2)
        soil_temperature = round(random.uniform(-10.0, 60.0), 2)
        watermark_frequency = round(random.uniform(0, 9999), 2)
        watermark = round(random.uniform(0, 300.0), 2)

        data.append((sensor_id, date_time, light_intensity, soil_temperature, watermark_frequency, watermark))

    return data


def cleanup_synthetic_rows(cursor, conn):
    cursor.execute("DELETE FROM agricsensors WHERE date_time >= %s", (SYNTHETIC_START,))
    conn.commit()
    print(f"🧹 Cleaned up {cursor.rowcount:,} synthetic rows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark bulk-insert throughput for agricsensors.")
    parser.add_argument("--rows", type=int, default=500000, help="Number of synthetic rows to insert (default: 500000).")
    parser.add_argument("--keep-data", action="store_true", help="Skip deleting the synthetic rows after the benchmark.")
    args = parser.parse_args()

    config = load_config()

    # Port default (5433) matches csv_loader.py's DBPORT fallback.
    conn = psycopg2.connect(
        dbname=config.get("DBNAME"),
        user=config.get("DBUSER"),
        password=config.get("PASSWORD"),
        host=config.get("DBHOST", "localhost"),
        port=config.get("DBPORT", 5433),
    )
    cursor = conn.cursor()

    try:
        sensor_ids = fetch_active_sensor_ids(cursor)
        payload = generate_synthetic_payload(args.rows, sensor_ids)

        print(f"--- Starting Bulk Ingestion Test: {args.rows} rows ---")

        start_time = time.time()

        insert_query = """
            INSERT INTO agricsensors (sensor_id, date_time, light_intensity, soil_temperature, watermark_frequency, watermark)
            VALUES %s
            ON CONFLICT (sensor_id, date_time) DO NOTHING
        """
        psycopg2.extras.execute_values(cursor, insert_query, payload, page_size=10000)
        conn.commit()

        end_time = time.time()

        duration = end_time - start_time
        rows_per_second = args.rows / duration

        print("--------------------------------------------------")
        print("FINAL INGESTION METRICS:")
        print(f"Total Rows Inserted : {args.rows:,}")
        print(f"Total Time Taken    : {duration:.4f} seconds")
        print(f"Throughput          : {rows_per_second:,.0f} Rows Per Second (RPS)")
        print("--------------------------------------------------")

        if args.keep_data:
            print("Synthetic rows left in place (--keep-data). Timestamps start at 2099-01-01 UTC for easy identification.")
        else:
            cleanup_synthetic_rows(cursor, conn)
    finally:
        cursor.close()
        conn.close()
