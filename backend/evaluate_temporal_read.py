"""
Multi-timestamp read latency evaluation for the AgriSense hypertable.

For each of several time-window sizes, runs the same aggregation query
against the `agricsensors` hypertable multiple times and records latency
statistics (mean, median, stdev, min, max), plus row counts. Results are
printed to stdout and written to a CSV for later charting (e.g. latency
vs. window size in Plotly/matplotlib for the evaluation chapter).

Config is read from conf.json using the same flat key structure as
database.py (DBUSER, PASSWORD, DBHOST, DBPORT, DBNAME), so this script
stays consistent with the rest of the pipeline and doesn't silently fall
back to defaults if conf.json doesn't match.
"""

import csv
import json
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2

CONF_PATH = Path("conf.json")
OUTPUT_CSV = Path("temporal_read_results.csv")

# Number of timed repetitions per window, after warm-up.
REPETITIONS = 7

# Anchor date based on your most recently populated temporal chunks.
END_DATE = datetime(2026, 8, 20, 0, 0, 0)

INTERVALS = {
    "1 Hour": timedelta(hours=1),
    "1 Day": timedelta(days=1),
    "7 Days": timedelta(days=7),
    "14 Days": timedelta(days=14),
    "30 Days": timedelta(days=30),
}

QUERY = """
    SELECT sensor_id, AVG(soil_temperature), AVG(watermark)
    FROM agricsensors
    WHERE date_time >= %s AND date_time <= %s
    GROUP BY sensor_id;
"""


def load_config():
    with open(CONF_PATH, "r") as f:
        conf = json.load(f)

    required = ["DBUSER", "PASSWORD", "DBHOST", "DBPORT", "DBNAME"]
    missing = [k for k in required if k not in conf]
    if missing:
        raise KeyError(
            f"conf.json is missing required key(s): {missing}. "
            f"Expected the same flat structure used in database.py."
        )
    return conf


def connect(conf):
    return psycopg2.connect(
        dbname=conf["DBNAME"],
        user=conf["DBUSER"],
        password=conf["PASSWORD"],
        host=conf["DBHOST"],
        port=conf["DBPORT"],
    )


def time_query(cursor, start_date, end_date):
    t0 = time.perf_counter()
    cursor.execute(QUERY, (start_date, end_date))
    results = cursor.fetchall()
    t1 = time.perf_counter()
    return (t1 - t0) * 1000, len(results)


def run_extension_test():
    conf = load_config()
    conn = connect(conf)
    cursor = conn.cursor()

    rows_for_csv = []

    try:
        print("--- Multi-Timestamp Read Evaluation ---")
        print(f"{'Window':<10} | {'Rows':<6} | {'Mean ms':<9} | "
              f"{'Median ms':<10} | {'Stdev ms':<9} | {'Min ms':<8} | {'Max ms':<8}")

        for label, delta in INTERVALS.items():
            start_date = END_DATE - delta

            # Warm-up run (untimed): pulls chunk pages into cache so the
            # timed runs reflect steady-state / warm-cache latency, not
            # a one-off cold-disk read. If you instead want worst-case
            # cold-cache numbers, remove this and note it explicitly in
            # your methodology, since cache flushing between runs isn't
            # generally available on managed Postgres/Timescale.
            time_query(cursor, start_date, END_DATE)

            durations = []
            row_count = None
            for _ in range(REPETITIONS):
                duration_ms, row_count = time_query(cursor, start_date, END_DATE)
                durations.append(duration_ms)

            mean_ms = statistics.mean(durations)
            median_ms = statistics.median(durations)
            stdev_ms = statistics.stdev(durations) if len(durations) > 1 else 0.0
            min_ms = min(durations)
            max_ms = max(durations)

            print(f"{label:<10} | {row_count:<6} | {mean_ms:<9.2f} | "
                  f"{median_ms:<10.2f} | {stdev_ms:<9.2f} | {min_ms:<8.2f} | {max_ms:<8.2f}")

            rows_for_csv.append({
                "window": label,
                "window_seconds": int(delta.total_seconds()),
                "rows_returned": row_count,
                "repetitions": REPETITIONS,
                "mean_ms": round(mean_ms, 3),
                "median_ms": round(median_ms, 3),
                "stdev_ms": round(stdev_ms, 3),
                "min_ms": round(min_ms, 3),
                "max_ms": round(max_ms, 3),
            })

    finally:
        cursor.close()
        conn.close()

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_for_csv[0].keys()))
        writer.writeheader()
        writer.writerows(rows_for_csv)

    print(f"\nResults written to {OUTPUT_CSV.resolve()}")


if __name__ == "__main__":
    run_extension_test()
