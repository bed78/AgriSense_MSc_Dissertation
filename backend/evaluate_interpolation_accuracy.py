"""
evaluate_interpolation_accuracy.py

Leave-one-out cross-validation (LOOCV) for two interpolation methods --
IDW (Section 6.6, cursor probe) and Delaunay barycentric (Section 6.6,
mesh overlay) -- run against real data pulled directly from TimescaleDB.

This consolidates what used to be two separate scripts:

  * evaluate_interpolation_accuracy.py        (single best-covered window)
  * evaluate_interpolation_accuracy_multi.py  (many windows, pooled)

into one script with two modes, selected on the command line. All the
distance math, predictors, and window-selection logic are shared between
the two modes so they can't drift out of sync with each other or with the
frontend's cursor probe.

Usage:
    python evaluate_interpolation_accuracy.py            # single-window mode
    python evaluate_interpolation_accuracy.py --multi     # multi-window mode
    python evaluate_interpolation_accuracy.py --multi --max-windows 50 --min-coverage 15

Requires a conf.json in the same directory. Either key style works:
    {"dbname": "...", "user": "...", "password": "...", "host": "localhost", "port": 5432}
    {"DBNAME": "...", "DBUSER": "...", "PASSWORD": "...", "DBHOST": "...", "DBPORT": ...}
"""

import argparse
import json
import math
import sys

import numpy as np
import pandas as pd
import psycopg2
from scipy.spatial import Delaunay

METRICS = ("temp", "watermark")
IDW_POWER = 2  # inverse-DISTANCE-squared, matching the frontend's cursor probe


# ============================================================================
# CONFIG / CONNECTION
# ============================================================================

def load_config(path="conf.json"):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: {path} not found. Create it with dbname/user/password/host/port.")
        sys.exit(1)


def connect(config):
    # Accept both lowercase keys (dbname/user/password/host/port) and the
    # DB-prefixed uppercase style used elsewhere in this project's conf.json
    # (DBNAME/DBUSER/PASSWORD/DBHOST/DBPORT), so this script works either way.
    def pick(*keys, default=None):
        for k in keys:
            if k in config and config[k] not in (None, ""):
                return config[k]
        return default

    dbname = pick("dbname", "DBNAME")
    user = pick("user", "DBUSER", "USER")
    password = pick("password", "PASSWORD")
    host = pick("host", "DBHOST", default="localhost")
    port = pick("port", "DBPORT", default=5432)

    missing = [name for name, val in
               [("dbname", dbname), ("user", user), ("password", password)] if val is None]
    if missing:
        print(f"Error: conf.json is missing required field(s): {missing}. "
              f"Found keys: {list(config.keys())}")
        sys.exit(1)

    try:
        return psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port)
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)


# ============================================================================
# SHARED DISTANCE / PROJECTION UTILITIES
# ============================================================================

def get_distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (Haversine), matching the frontend's
    cursor-probe implementation (Section 6.6)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def project_to_local_xy(lat, lon, ref_lat):
    """Local equirectangular projection for Delaunay triangulation. Adequate
    over a site this small (a few hundred metres to ~1km, Chapter 8). Verify
    against InterpolationCanvas's actual projection before treating hull
    membership results as final -- see accompanying notes.

    Used for BOTH predictors' triangulation step, unlike the old multi-window
    script, which triangulated on raw (lon, lat) pairs -- that skips this
    projection and distorts hull shape away from the equator. Keeping one
    projection function shared here avoids that drift.
    """
    R = 6371000.0
    x = np.radians(lon) * R * np.cos(np.radians(ref_lat))
    y = np.radians(lat) * R
    return x, y


# ============================================================================
# PREDICTOR 1: INVERSE DISTANCE WEIGHTING (cursor probe, Section 6.6)
# ============================================================================

def calculate_idw(target_lat, target_lon, reference_nodes, target_metric="temp", power=IDW_POWER):
    numerator = 0.0
    denominator = 0.0

    for _, node in reference_nodes.iterrows():
        if pd.isna(node[target_metric]):
            continue

        dist = get_distance_km(target_lat, target_lon, node["lat"], node["lon"])

        if dist < 0.001:
            return node[target_metric]

        weight = 1.0 / (dist ** power)
        numerator += weight * node[target_metric]
        denominator += weight

    if denominator == 0:
        return None

    return numerator / denominator


# ============================================================================
# PREDICTOR 2: DELAUNAY BARYCENTRIC (mesh overlay, Section 6.6)
# ============================================================================

def _barycentric_weights(tri, simplex_index, xy_point):
    b = tri.transform[simplex_index, :2].dot(xy_point - tri.transform[simplex_index, 2])
    return np.array([b[0], b[1], 1 - b.sum()])


def calculate_barycentric(target_lat, target_lon, reference_nodes, target_metric="temp"):
    """Returns None if fewer than 3 reference nodes are available, or if the
    target point falls outside the convex hull of reference_nodes (no
    extrapolation, matching NFR-03)."""
    valid = reference_nodes.dropna(subset=[target_metric]).copy()
    if len(valid) < 3:
        return None

    ref_lat_mean = valid["lat"].mean()
    xs, ys = project_to_local_xy(valid["lat"].values, valid["lon"].values, ref_lat_mean)
    points = np.column_stack([xs, ys])

    try:
        tri = Delaunay(points)
    except Exception:
        return None

    tx, ty = project_to_local_xy(np.array([target_lat]), np.array([target_lon]), ref_lat_mean)
    target_xy = np.array([tx[0], ty[0]])

    simplex_index = tri.find_simplex(target_xy)
    if simplex_index == -1:
        return None

    weights = _barycentric_weights(tri, simplex_index, target_xy)
    vertex_indices = tri.simplices[simplex_index]
    vertex_values = valid.iloc[vertex_indices][target_metric].values

    return float(np.dot(weights, vertex_values))


PREDICTORS = {"idw": calculate_idw, "barycentric": calculate_barycentric}


# ============================================================================
# LOOCV CORE (shared by single- and multi-window modes)
# ============================================================================

def run_loocv(df, metric="temp", predictor="idw", verbose=True):
    """
    Runs LOOCV on one snapshot dataframe using the named predictor
    ('idw' or 'barycentric'). Returns a dict:
        {mae, rmse, n_predicted, n_total, skipped, abs_errors}
    """
    predict_fn = PREDICTORS[predictor]
    label = "IDW" if predictor == "idw" else "Barycentric"

    actuals, predictions, skipped = [], [], []
    valid_nodes = df.dropna(subset=[metric]).copy()
    n = len(valid_nodes)

    if verbose:
        print(f"\n--- Running {label} LOOCV for {metric.upper()} across {n} nodes ---")

    for idx, target_node in valid_nodes.iterrows():
        true_value = target_node[metric]
        target_lat = target_node["lat"]
        target_lon = target_node["lon"]
        training_set = valid_nodes.drop(idx)

        predicted_value = predict_fn(target_lat, target_lon, training_set, target_metric=metric)

        if predicted_value is not None:
            actuals.append(true_value)
            predictions.append(predicted_value)
            if verbose:
                error = abs(true_value - predicted_value)
                print(f"  Node {target_node['sensor_id']:<6} | Actual: {true_value:>7.2f} | "
                      f"Predicted: {predicted_value:>7.2f} | Error: {error:>5.2f}")
        else:
            skipped.append(target_node["sensor_id"])
            if verbose:
                reason = "outside convex hull" if predictor == "barycentric" else "no valid reference nodes"
                print(f"  Node {target_node['sensor_id']:<6} | SKIPPED ({reason})")

    if not actuals:
        if verbose:
            print(f"  No predictable nodes for {label}/{metric}. Skipped: {skipped}")
        return {"mae": None, "rmse": None, "n_predicted": 0, "n_total": n,
                "skipped": skipped, "abs_errors": []}

    actuals = np.array(actuals)
    predictions = np.array(predictions)
    abs_errors = np.abs(actuals - predictions)
    mae = float(abs_errors.mean())
    rmse = float(np.sqrt(np.mean((actuals - predictions) ** 2)))

    if verbose:
        print(f"  {label}/{metric.upper()}: MAE={mae:.4f}  RMSE={rmse:.4f}  "
              f"n={len(actuals)}/{n}  skipped={len(skipped)}")

    return {"mae": mae, "rmse": rmse, "n_predicted": len(actuals), "n_total": n,
            "skipped": skipped, "abs_errors": abs_errors.tolist()}


# ============================================================================
# WINDOW SELECTION / SNAPSHOT LOADING (shared)
# ============================================================================

def find_windows(conn, min_nodes=10, max_windows=100, earliest_plausible="2022-06-01",
                  bucket_interval="24 hours"):
    """
    Finds the best-covered time WINDOW(s) rather than an exact timestamp.

    A first real run against this database confirmed that no two sensors
    ever share an identical microsecond-precision date_time -- every exact
    timestamp in agricsensors has exactly 1 reporting node. Exact-match
    grouping can therefore never find a multi-sensor snapshot; this is
    expected behaviour for real, independently-clocked LoRaWAN hardware,
    not a bug. This mirrors the approach the real /sensors/timeline
    endpoint already uses (Section 6.2): bucket date_time into fixed
    windows via TimescaleDB's time_bucket(), then count DISTINCT sensors
    per bucket.

    Excludes timestamps before `earliest_plausible`: a first run also
    surfaced 1970-01-01 00:00:00+00 (Unix epoch zero) as a "reporting"
    timestamp for a handful of rows -- almost certainly a NULL/malformed
    date_time coerced to epoch-zero somewhere in the ingestion pipeline.
    Negligible at this scale, but real; worth a one-line mention rather
    than silent discarding.

    With max_windows=1 this reproduces the old single-window script's
    "best snapshot" behaviour; with max_windows>1 it reproduces the old
    multi-window script's "top-N covered windows" behaviour.
    """
    probe = pd.read_sql_query("""
        SELECT
            time_bucket(%s, date_time) AS bucket,
            COUNT(DISTINCT sensor_id) AS n_nodes
        FROM agricsensors
        WHERE date_time >= %s
        GROUP BY bucket
        HAVING COUNT(DISTINCT sensor_id) >= %s
        ORDER BY n_nodes DESC, bucket ASC
        LIMIT %s
    """, conn, params=(bucket_interval, earliest_plausible, min_nodes, max_windows))

    if probe.empty:
        print(f"No windows found with coverage >= {min_nodes} nodes at or after "
              f"{earliest_plausible}. Lower min_nodes/coverage or widen bucket_interval.")
        sys.exit(1)

    bad_count = pd.read_sql_query(
        "SELECT COUNT(*) AS n FROM agricsensors WHERE date_time < %s",
        conn, params=(earliest_plausible,)
    ).iloc[0]["n"]
    total_count = pd.read_sql_query("SELECT COUNT(*) AS n FROM agricsensors", conn).iloc[0]["n"]
    if bad_count > 0:
        pct = 100 * bad_count / total_count if total_count else 0
        print(f"NOTE: {bad_count}/{total_count} rows ({pct:.4f}%) in agricsensors have "
              f"date_time before {earliest_plausible} and were excluded.")

    print(f"Covered {bucket_interval} windows found (showing up to {max_windows}):")
    print(probe.to_string(index=False))

    return probe  # columns: bucket, n_nodes


def load_snapshot(conn, bucket_start, bucket_interval="24 hours", aggregate=False):
    """
    Loads one row per sensor for the [bucket_start, bucket_start + interval)
    window, joined against sensor_coordinates for lat/lon.

    aggregate=False (single-window mode): DISTINCT ON latest reading per
    sensor in the window -- the same concept the Sensor Health page's
    "last heartbeat" displays.

    aggregate=True (multi-window mode): AVG of all readings per sensor in
    the window, so one noisy reading doesn't dominate a short window.
    """
    if aggregate:
        query = """
            SELECT
                a.sensor_id,
                c.latitude  AS lat,
                c.longitude AS lon,
                AVG(a.soil_temperature) AS temp,
                AVG(a.watermark)        AS watermark
            FROM agricsensors a
            JOIN sensor_coordinates c ON a.sensor_id = c.sensor_id
            WHERE a.date_time >= %s
              AND a.date_time < %s::timestamptz + %s::interval
            GROUP BY a.sensor_id, c.latitude, c.longitude
        """
    else:
        query = """
            SELECT DISTINCT ON (a.sensor_id)
                a.sensor_id,
                c.latitude  AS lat,
                c.longitude AS lon,
                a.soil_temperature AS temp,
                a.watermark
            FROM agricsensors a
            JOIN sensor_coordinates c ON a.sensor_id = c.sensor_id
            WHERE a.date_time >= %s
              AND a.date_time < %s::timestamptz + %s::interval
            ORDER BY a.sensor_id, a.date_time DESC
        """
    return pd.read_sql_query(query, conn, params=(bucket_start, bucket_start, bucket_interval))


# ============================================================================
# MODE 1: SINGLE BEST-COVERED WINDOW
# ============================================================================

def run_single_window(conn, bucket_interval, min_nodes):
    probe = find_windows(conn, min_nodes=min_nodes, max_windows=20, bucket_interval=bucket_interval)
    best = probe.iloc[0]
    bucket_start = best["bucket"]

    if best["n_nodes"] < min_nodes:
        print(f"\nWarning: best available {bucket_interval} window only covers "
              f"{best['n_nodes']} distinct nodes (requested minimum {min_nodes}). "
              f"Consider widening bucket_interval (e.g. '1 day') if this is too thin.")

    df_snapshot = load_snapshot(conn, bucket_start, bucket_interval, aggregate=False)
    snapshot_time = f"{bucket_start} (window: {bucket_interval})"
    print(f"\nUsing snapshot window starting {bucket_start}, width {bucket_interval} "
          f"({len(df_snapshot)} distinct nodes reporting)")

    results = {}
    for metric in METRICS:
        for predictor in PREDICTORS:
            results[(metric, predictor)] = run_loocv(df_snapshot, metric=metric, predictor=predictor)

    print("\n" + "=" * 78)
    print(f"{'Metric':<12}{'Predictor':<14}{'MAE':>10}{'RMSE':>10}{'n predicted':>15}{'n skipped':>12}")
    print("-" * 78)
    for (metric, predictor), r in results.items():
        mae_str = f"{r['mae']:.4f}" if r["mae"] is not None else "n/a"
        rmse_str = f"{r['rmse']:.4f}" if r["rmse"] is not None else "n/a"
        print(f"{metric:<12}{predictor:<14}{mae_str:>10}{rmse_str:>10}"
              f"{r['n_predicted']:>10}/{r['n_total']:<4}{len(r['skipped']):>12}")
    print("=" * 78)

    rows = []
    for (metric, predictor), r in results.items():
        rows.append({
            "metric": metric,
            "predictor": predictor,
            "mae": r["mae"],
            "rmse": r["rmse"],
            "n_predicted": r["n_predicted"],
            "n_total": r["n_total"],
            "n_skipped": len(r["skipped"]),
            "skipped_nodes": ";".join(r["skipped"]),
            "snapshot_timestamp": str(snapshot_time),
        })
    out_path = "loocv_results.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"\nResults written to {out_path} copy to section 6.6 of the report.")

    return results


# ============================================================================
# MODE 2: MULTI-WINDOW, POOLED
# ============================================================================

def run_multi_window(conn, bucket_interval, min_nodes, max_windows):
    probe = find_windows(conn, min_nodes=min_nodes, max_windows=max_windows, bucket_interval=bucket_interval)
    print(f"\nEvaluating {len(probe)} windows (width={bucket_interval}, min coverage={min_nodes} nodes)")

    all_rows = []
    for window_index, row in enumerate(probe.itertuples(index=False), start=1):
        bucket_start, coverage = row.bucket, row.n_nodes
        df_snapshot = load_snapshot(conn, bucket_start, bucket_interval, aggregate=True)

        for metric in METRICS:
            for predictor in PREDICTORS:
                result = run_loocv(df_snapshot, metric=metric, predictor=predictor, verbose=False)
                predicted_ids = [sid for sid in df_snapshot.dropna(subset=[metric])["sensor_id"]
                                 if sid not in result["skipped"]]
                for sid, err in zip(predicted_ids, result["abs_errors"]):
                    all_rows.append({
                        "window_index": window_index,
                        "window_start": bucket_start,
                        "coverage": coverage,
                        "metric": metric,
                        "predictor": predictor,
                        "sensor_id": sid,
                        "abs_error": err,
                        "skipped": False,
                    })
                for sid in result["skipped"]:
                    all_rows.append({
                        "window_index": window_index,
                        "window_start": bucket_start,
                        "coverage": coverage,
                        "metric": metric,
                        "predictor": predictor,
                        "sensor_id": sid,
                        "abs_error": None,
                        "skipped": True,
                    })

    if not all_rows:
        print("No predictions were generated; nothing to summarise.")
        return {}

    by_node_df = pd.DataFrame(all_rows)
    by_node_path = "interpolation_accuracy_multi_by_node.csv"
    by_node_df.to_csv(by_node_path, index=False)

    summary_rows = []
    for (predictor, metric), group in by_node_df.groupby(["predictor", "metric"]):
        predicted = group[~group["skipped"]]
        errors = predicted["abs_error"].values

        per_window_mae = predicted.groupby("window_index")["abs_error"].mean()

        summary_rows.append({
            "predictor": predictor,
            "metric": metric,
            "n_windows": group["window_index"].nunique(),
            "n_predicted": len(predicted),
            "n_skipped": int(group["skipped"].sum()),
            "pooled_mae": float(errors.mean()) if len(errors) else None,
            "pooled_rmse": float(np.sqrt(np.mean(errors ** 2))) if len(errors) else None,
            "mean_of_window_mae": float(per_window_mae.mean()) if len(per_window_mae) else None,
            "median_of_window_mae": float(per_window_mae.median()) if len(per_window_mae) else None,
            "stdev_of_window_mae": float(per_window_mae.std()) if len(per_window_mae) > 1 else None,
        })

    summary_df = pd.DataFrame(summary_rows)
    summary_path = "interpolation_accuracy_multi_summary.csv"
    summary_df.to_csv(summary_path, index=False)

    print(f"\n{'Metric':<18} | {'Predictor':<12} | {'Windows':<8} | {'n Pred':<7} | "
          f"{'n Skip':<7} | {'Pooled MAE':<11} | {'Pooled RMSE':<12} | "
          f"{'Mean win. MAE':<14} | {'Stdev win. MAE':<15}")
    for _, s in summary_df.iterrows():
        mae = f"{s['pooled_mae']:.3f}" if s["pooled_mae"] is not None else "n/a"
        rmse = f"{s['pooled_rmse']:.3f}" if s["pooled_rmse"] is not None else "n/a"
        mwm = f"{s['mean_of_window_mae']:.3f}" if s["mean_of_window_mae"] is not None else "n/a"
        swm = f"{s['stdev_of_window_mae']:.3f}" if s["stdev_of_window_mae"] is not None else "n/a"
        print(f"{s['metric']:<18} | {s['predictor']:<12} | {s['n_windows']:<8} | "
              f"{s['n_predicted']:<7} | {s['n_skipped']:<7} | {mae:<11} | {rmse:<12} | "
              f"{mwm:<14} | {swm:<15}")

    print(f"\nPer-node predictions written to {by_node_path}")
    print(f"Summary written to {summary_path}")

    return summary_rows


# ============================================================================
# MAIN
# ============================================================================

def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--multi", action="store_true",
                    help="Evaluate many well-covered windows and pool results, instead of just the single best one.")
    p.add_argument("--bucket-interval", default="12 hours",
                    help="TimescaleDB time_bucket() width, e.g. '6 hours', '12 hours', '1 day'. Default: 12 hours.")
    p.add_argument("--min-coverage", type=int, default=10,
                    help="Minimum distinct reporting sensors required to keep a window. Default: 10.")
    p.add_argument("--max-windows", type=int, default=100,
                    help="Multi-window mode only: cap on number of windows evaluated. Default: 100.")
    return p.parse_args()


def main():
    args = parse_args()
    config = load_config()
    conn = connect(config)

    try:
        if args.multi:
            results = run_multi_window(conn, args.bucket_interval, args.min_coverage, args.max_windows)
        else:
            results = run_single_window(conn, args.bucket_interval, args.min_coverage)
    finally:
        conn.close()

    return results


if __name__ == "__main__":
    main()
