"""
AgriSense API ("Devil's Bridge API").

FastAPI backend that serves sensor network data to the frontend map/dashboard:
- Live network health / sensor listings
- Time-bucketed historical readings
- Delaunay triangulation mesh + convex hull for spatial interpolation
- Cross-section "slice" interpolation along an arbitrary path
- CSV / PDF export endpoints (single sensor, multi-sensor batch, and slice)

Run with: `uvicorn main:app --reload`
"""

import io
import uuid
import asyncio
import tempfile
import os
import csv
import traceback
import numpy as np
from datetime import datetime, date, timezone, timedelta
from typing import List

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from scipy.spatial import Delaunay, ConvexHull
from scipy.interpolate import LinearNDInterpolator

import matplotlib
matplotlib.use('Agg')  # non-interactive backend — required for chart generation on a server
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from fpdf import FPDF

from database import get_db

app = FastAPI(title="Devil's Bridge API")

# --- THE FIX: Add CORS permission slip ---
# Allows the React dev server (running on a different port) to call this API
# from the browser without being blocked by same-origin policy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], # React's exact address
    allow_credentials=True,
    allow_methods=["*"], # Allow GET, POST, PUT, DELETE
    allow_headers=["*"], # Allow all headers
)

# ... the rest of your routes go down here ...


class SliceRequest(BaseModel):
    """Request body for POST /slice/compute — an ordered list of sensor
    IDs (or raw "lat,lon" waypoints) defining the cross-section path."""
    sensor_ids: List[str]


# Serializes PDF generation so concurrent export requests don't stomp on
# each other's temp chart image files or matplotlib's shared global state.
pdf_lock = asyncio.Lock()

# --- HELPER FUNCTIONS ---


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Great-circle distance between two lat/lon points, in metres.

    Uses the haversine formula with Earth's mean radius. Used to build a
    cumulative distance axis when walking along a cross-section path.
    """
    R = 6371000  # Earth's mean radius, in metres
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi, dlambda = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlambda/2)**2
    return 2 * R * np.arctan2(np.sqrt(a), np.sqrt(1-a))


async def get_slice_data(sensor_ids: List[str], date_from: date, date_to: date, db: AsyncSession):
    """Reusable logic to interpolate cross-section data for both API and Exports"""
    # Load every known sensor's coordinates, keyed by lowercase sensor_id
    # for case-insensitive matching against incoming request IDs.
    coords_result = await db.execute(text("SELECT sensor_id, latitude, longitude FROM sensor_coordinates"))
    # Normalize dictionary keys to lowercase for safe matching
    coords = {str(r.sensor_id).lower(): (r.latitude, r.longitude) for r in coords_result.fetchall() if r.latitude and r.longitude}

    # Average each sensor's readings over the requested date range — this
    # is what gets interpolated across the network (not raw point-in-time values).
    r = await db.execute(text("""
        SELECT sensor_id, AVG(soil_temperature) as avg_temp, AVG(watermark) as avg_wm
        FROM agricsensors WHERE date_time BETWEEN :t1 AND :t2 GROUP BY sensor_id
    """), {"t1": date_from, "t2": date_to})

    # Build parallel arrays of (lat, lon) points and their averaged readings,
    # skipping any sensor missing coordinates or data for this window.
    pts, temps, wms = [], [], []
    for row in r.fetchall():
        sid = str(row.sensor_id).lower()
        if sid in coords and row.avg_temp is not None and row.avg_wm is not None:
            pts.append(coords[sid])
            temps.append(row.avg_temp)
            wms.append(row.avg_wm)

    # Delaunay-based linear interpolation needs at least 3 non-collinear
    # points to define a triangulated surface.
    if len(pts) < 3:
        raise ValueError("Not enough network data to build Delaunay triangulation.")

    pts, temps, wms = np.array(pts), np.array(temps), np.array(wms)
    # Build continuous interpolating surfaces over the sensor network for
    # both metrics, so any point inside the hull can be estimated.
    temp_interp = LinearNDInterpolator(pts, temps)
    wm_interp = LinearNDInterpolator(pts, wms)

    # ----------------------------------------------------------------------
    # FIX: Hybrid Pathing Engine! 
    # This safely processes standard sensor IDs OR custom arbitrary waypoints
    # ----------------------------------------------------------------------
    # Resolve each requested waypoint to a concrete (lat, lon) coordinate.
    # A waypoint can either be a known sensor ID or a raw "lat,lon" string
    # from an arbitrary point the user clicked on the map.
    path_pts = []
    for sid in sensor_ids:
        if ',' in sid:
            # It's an arbitrary map click (e.g., "52.37,-3.77")
            parts = sid.split(',')
            path_pts.append((float(parts[0]), float(parts[1])))
        else:
            # It's a standard hardware node (e.g., "ff-01")
            clean_sid = sid.lower()
            if clean_sid in coords:
                path_pts.append(coords[clean_sid])

    # A cross-section needs at least a start and end point.
    if len(path_pts) < 2:
        raise ValueError("Need at least 2 valid points on the map to create a slice.")

    # Walk each consecutive pair of waypoints, sampling points along the
    # straight line between them proportional to its real-world distance,
    # and accumulate a running total distance to use as the X-axis.
    slice_lats, slice_lons, distances = [], [], []
    total_dist = 0

    for i in range(len(path_pts)-1):
        p1, p2 = path_pts[i], path_pts[i+1]
        dist = haversine_distance(p1[0], p1[1], p2[0], p2[1])
        # Sample density scales with segment length (100 samples per km),
        # with a floor of 10 samples so short segments are still resolved.
        steps = max(10, int((dist / 1000) * 100))

        lats, lons = np.linspace(p1[0], p2[0], steps), np.linspace(p1[1], p2[1], steps)

        for j in range(len(lats)):
            # Skip the first sample of every segment after the first, since
            # it's identical to the previous segment's last point (shared vertex).
            if j == 0 and i > 0: continue
            slice_lats.append(lats[j])
            slice_lons.append(lons[j])
            if len(slice_lats) > 1:
                total_dist += haversine_distance(slice_lats[-2], slice_lons[-2], slice_lats[-1], slice_lons[-1])
            distances.append(total_dist)

    # Interpolate both metrics at every sampled point along the path.
    sample_pts = np.column_stack((slice_lats, slice_lons))
    interp_temps = temp_interp(sample_pts)
    interp_wms = wm_interp(sample_pts)

    # Points outside the convex hull of the sensor network can't be
    # interpolated and come back as NaN — convert those to None for JSON/CSV output.
    temp_list = [t if not np.isnan(t) else None for t in interp_temps]
    wm_list = [w if not np.isnan(w) else None for w in interp_wms]

    return len(path_pts), len(pts), distances, temp_list, wm_list


def render_pdf_with_chart(chart_buffer: io.BytesIO, build_pdf_body) -> bytes:
    """
    Shared PDF export plumbing: writes the chart PNG to a temp file,
    builds the PDF via the supplied callback, cleans up the temp file,
    and returns the final PDF bytes. Used by both the single-sensor and
    slice export endpoints to avoid duplicating the same boilerplate.
    """
    tmp = os.path.join(tempfile.gettempdir(), f"chart_{uuid.uuid4().hex}.png")
    with open(tmp, "wb") as f:
        f.write(chart_buffer.getbuffer())
    try:
        # build_pdf_body is a caller-supplied function that takes the temp
        # image path and returns a fully-built FPDF object (so each export
        # endpoint can add its own headers/labels around the shared chart).
        pdf = build_pdf_body(tmp)
        output = pdf.output()
        pdf_bytes = bytes(output) if not hasattr(output, 'encode') else output.encode('latin-1')
        return pdf_bytes
    finally:
        # Always clean up the temp PNG, even if PDF generation raised.
        if os.path.exists(tmp):
            os.remove(tmp)


def generate_sensor_chart(dates, temps, watermarks, sensor_id: str) -> io.BytesIO:
    """
    Build a dual-axis line chart (soil temperature + watermark over time)
    for a single sensor and return it as an in-memory PNG buffer.
    """
    fig, ax1 = plt.subplots(figsize=(10, 5))
    color1, color2 = '#E8593C', '#378ADD'
    ax1.set_xlabel('Date & Time')
    ax1.set_ylabel('Soil Temperature (°C)', color=color1, fontweight='bold')
    ax1.plot(dates, temps, color=color1, linewidth=2)
    ax1.tick_params(axis='y', labelcolor=color1)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.xticks(rotation=45)
    # Second Y-axis sharing the same X-axis, for the watermark series.
    ax2 = ax1.twinx()
    ax2.set_ylabel('Watermark (cb)', color=color2, fontweight='bold')
    ax2.plot(dates, watermarks, color=color2, linewidth=2)
    ax2.tick_params(axis='y', labelcolor=color2)
    plt.title(f"Sensor Readings Profile: {sensor_id.upper()}", fontsize=14, pad=15)
    ax1.grid(True, linestyle='--', alpha=0.6)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=300)
    buf.seek(0)
    plt.close(fig)  # free matplotlib's figure memory
    return buf


def generate_comparative_chart(data_by_sensor: dict, sids: list) -> io.BytesIO:
    """Generates a single chart with multiple sensors overlaid for comparison."""
    fig, ax1 = plt.subplots(figsize=(12, 7))
    ax2 = ax1.twinx()

    ax1.set_xlabel('Date & Time', fontweight='bold')
    ax1.set_ylabel('Soil Temperature (°C)', fontweight='bold')
    ax2.set_ylabel('Watermark (cb)', fontweight='bold')

    # 1. Define a robust list of distinct geometric markers
    markers = ['o', 's', '^', 'D', 'v', 'p', '*', 'h', 'X', '<']

    # 2. Generate a distinct color map
    colors = plt.cm.tab20(np.linspace(0, 1, max(len(sids), 1)))

    for idx, sid in enumerate(sids):
        data = data_by_sensor.get(sid)
        if not data or not data["dates"]:
            continue

        color = colors[idx]
        # Assign a distinct geometric shape to this specific sensor
        marker = markers[idx % len(markers)]

        # TEMPERATURE: Solid Line, Filled Geometric Marker
        ax1.plot(data["dates"], data["temps"], label=f"{sid.upper()} Temp", 
                 color=color, linestyle='-', marker=marker, linewidth=2, markersize=7)

        # WATERMARK: Dashed Line, Hollow Geometric Marker (fillstyle='none')
        ax2.plot(data["dates"], data["wms"], label=f"{sid.upper()} Watermark", 
                 color=color, linestyle='--', marker=marker, linewidth=2, markersize=7, fillstyle='none')

    # Formatting
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.xticks(rotation=45)
    plt.title("Comparative Multi-Node Analysis", fontsize=14, pad=15)
    ax1.grid(True, linestyle='-', alpha=0.3) # Softened the grid so lines pop more

    # Combine the legends from both axes and put them at the bottom
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper center', 
               bbox_to_anchor=(0.5, -0.15), ncol=max(2, len(sids)), frameon=False)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    return buf


def generate_slice_chart(distances, temps, watermarks, path_str: str) -> io.BytesIO:
    """Specialized chart generator for distance-based cross-sections"""
    fig, ax1 = plt.subplots(figsize=(10, 5))
    color1, color2 = '#E8593C', '#378ADD'
    ax1.set_xlabel('Cross-Section Distance (m)')
    ax1.set_ylabel('Avg Soil Temperature (°C)', color=color1, fontweight='bold')

    # Filter out None values before plotting (matplotlib can't handle
    # None the way it handles NaN for gap-in-line rendering here).
    valid_t = [(d, t) for d, t in zip(distances, temps) if t is not None]
    if valid_t:
        d_t, t_t = zip(*valid_t)
        ax1.plot(d_t, t_t, color=color1, linewidth=2)

    ax1.tick_params(axis='y', labelcolor=color1)
    ax2 = ax1.twinx()
    ax2.set_ylabel('Avg Watermark (cb)', color=color2, fontweight='bold')

    valid_w = [(d, w) for d, w in zip(distances, watermarks) if w is not None]
    if valid_w:
        d_w, w_w = zip(*valid_w)
        ax2.plot(d_w, w_w, color=color2, linewidth=2)

    ax2.tick_params(axis='y', labelcolor=color2)
    plt.title(f"Topological Slice Path: {path_str}", fontsize=14, pad=15)
    ax1.grid(True, linestyle='--', alpha=0.6)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=300)
    buf.seek(0)
    plt.close(fig)
    return buf


# --- CORE ENDPOINTS ---

@app.get("/network/health")
async def network_health(db: AsyncSession = Depends(get_db)):
    """
    Return online/offline status for every registered sensor.

    A sensor is considered "Online" if it has reported a reading within
    the last 24 hours; otherwise it's "Offline" (or "Never" seen at all).
    """
    result = await db.execute(text("""
        SELECT c.sensor_id, c.latitude, c.longitude, MAX(a.date_time) as last_seen
        FROM sensor_coordinates c
        LEFT JOIN agricsensors a ON c.sensor_id = a.sensor_id
        GROUP BY c.sensor_id, c.latitude, c.longitude
    """))
    nodes = []
    now = datetime.now(timezone.utc)
    for r in result.fetchall():
        is_online = False
        last_seen_str = "Never"
        if r.last_seen:
            is_online = (now - r.last_seen).total_seconds() <= 86400  # 24 hours
            last_seen_str = r.last_seen.strftime("%Y-%m-%d %H:%M")
        nodes.append({
            "id": r.sensor_id.upper(), "status": "Online" if is_online else "Offline",
            "last_seen": last_seen_str, "lat": round(r.latitude, 4) if r.latitude else "--", "lon": round(r.longitude, 4) if r.longitude else "--"
        })
    return nodes


@app.get("/sensors")
async def list_sensors(db: AsyncSession = Depends(get_db)):
    """Return every sensor's static coordinates (for placing map markers)."""
    # Added WHERE clause to prevent null coordinates from crashing the frontend map
    result = await db.execute(text("""
        SELECT sensor_id, latitude as lat, longitude as lon, altitude as alt 
        FROM sensor_coordinates 
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """))
    return [dict(r._mapping) for r in result]


@app.get("/sensors/timeline")
async def get_sensor_timeline(date_from: date = Query(...), date_to: date = Query(...), db: AsyncSession = Depends(get_db)):
    """
    Return per-day averaged readings for every sensor in the date range,
    shaped for a frontend timeline slider that animates the map over time.
    """
    try:
        # Get daily averages for all sensors to feed the map animation slider
        result = await db.execute(text("""
            SELECT 
                a.sensor_id, 
                DATE(a.date_time) as obs_date,
                c.latitude as lat, 
                c.longitude as lon,
                AVG(a.soil_temperature) as temp, 
                AVG(a.watermark) as watermark
            FROM agricsensors a
            JOIN sensor_coordinates c ON LOWER(a.sensor_id) = LOWER(c.sensor_id)
            WHERE a.date_time >= :t1 AND a.date_time <= :t2
            AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL
            GROUP BY a.sensor_id, obs_date, c.latitude, c.longitude
            ORDER BY obs_date ASC
        """), {"t1": date_from, "t2": date_to + timedelta(days=1)})  # +1 day makes date_to inclusive

        rows = result.fetchall()

        # Format the data cleanly for the React slider: { "YYYY-MM-DD": { "FF-01": { lat, lon, temp, watermark } } }
        timeline = {}
        for r in rows:
            date_str = r.obs_date.isoformat()
            if date_str not in timeline:
                timeline[date_str] = {}
            timeline[date_str][r.sensor_id.upper()] = {
                "lat": r.lat,
                "lon": r.lon,
                "temp":      round(r.temp,      2) if r.temp      is not None else None,
                "watermark": round(r.watermark, 2) if r.watermark is not None else None,
            }
        return timeline
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sensors/mesh")
async def get_sensor_mesh(date_from: date = Query(...), date_to: date = Query(...), db: AsyncSession = Depends(get_db)):
    """
    Returns the Delaunay triangulation of all sensors that have data in the
    requested date range, as a list of triangle edges [[lat,lon],[lat,lon]].
    The frontend draws these as Polylines to show the interpolation mesh.
    Only sensors with actual readings are included — offline nodes are excluded
    so the mesh reflects the live coverage area accurately.
    """
    try:
        result = await db.execute(text("""
            SELECT c.latitude as lat, c.longitude as lon
            FROM sensor_coordinates c
            INNER JOIN agricsensors a ON LOWER(a.sensor_id) = LOWER(c.sensor_id)
            WHERE a.date_time >= :t1 AND a.date_time <= :t2
              AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL
            GROUP BY c.latitude, c.longitude
        """), {"t1": date_from, "t2": date_to + timedelta(days=1)})

        pts = [(r.lat, r.lon) for r in result.fetchall()]
        if len(pts) < 3:
            return {"edges": []}  # need at least 3 points to triangulate

        arr = np.array(pts)
        tri = Delaunay(arr)

        # Extract unique edges from all triangles (each simplex is 3 vertex indices)
        edges = set()
        for simplex in tri.simplices:
            for i in range(3):
                a, b = simplex[i], simplex[(i + 1) % 3]
                edges.add((min(a, b), max(a, b)))  # normalize direction so shared edges dedupe

        edge_list = [
            [[float(arr[a][0]), float(arr[a][1])], [float(arr[b][0]), float(arr[b][1])]]
            for a, b in edges
        ]
        return {"edges": edge_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sensors/triangles")
async def get_sensor_triangles(date_from: date = Query(...), date_to: date = Query(...), db: AsyncSession = Depends(get_db)):
    """
    Returns the full Delaunay triangulation as a list of triangles, each with
    three vertices that include both geospatial coordinates and the averaged
    sensor readings for that node over the date range. Used by the frontend
    canvas overlay to render continuous colour-gradient interpolation fills.
    """
    try:
        result = await db.execute(text("""
            SELECT
                c.sensor_id,
                c.latitude  AS lat,
                c.longitude AS lon,
                AVG(a.soil_temperature) AS avg_temp,
                AVG(a.watermark)        AS avg_watermark
            FROM sensor_coordinates c
            INNER JOIN agricsensors a ON LOWER(a.sensor_id) = LOWER(c.sensor_id)
            WHERE a.date_time >= :t1 AND a.date_time <= :t2
              AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL
            GROUP BY c.sensor_id, c.latitude, c.longitude
        """), {"t1": date_from, "t2": date_to + timedelta(days=1)})

        rows = result.fetchall()
        if len(rows) < 3:
            return {"triangles": []}

        pts   = np.array([[r.lat, r.lon] for r in rows])
        tri   = Delaunay(pts)
        # Build a lookup of enriched node data (coords + averaged readings)
        # so each triangle's vertices carry both geometry and values.
        nodes = [
            {
                "lat": float(r.lat), "lon": float(r.lon),
                "temp": float(r.avg_temp) if r.avg_temp is not None else None,
                "watermark": float(r.avg_watermark) if r.avg_watermark is not None else None,
            }
            for r in rows
        ]

        triangles = [
            [nodes[i] for i in simplex]
            for simplex in tri.simplices.tolist()
        ]
        return {"triangles": triangles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sensors/hull")
async def get_sensor_hull(date_from: date = Query(...), date_to: date = Query(...), db: AsyncSession = Depends(get_db)):
    """
    Returns the convex hull of all sensors with data in the requested date range
    as an ordered list of [lat, lon] vertices. The frontend uses this polygon
    to constrain IDW hover estimates — points outside the hull produce no output.
    """
    try:
        result = await db.execute(text("""
            SELECT c.latitude as lat, c.longitude as lon
            FROM sensor_coordinates c
            INNER JOIN agricsensors a ON LOWER(a.sensor_id) = LOWER(c.sensor_id)
            WHERE a.date_time >= :t1 AND a.date_time <= :t2
              AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL
            GROUP BY c.latitude, c.longitude
        """), {"t1": date_from, "t2": date_to + timedelta(days=1)})

        pts = [(r.lat, r.lon) for r in result.fetchall()]
        if len(pts) < 3:
            return {"hull": []}  # need at least 3 points to form a hull

        arr = np.array(pts)
        hull = ConvexHull(arr)
        ordered = [[float(arr[i][0]), float(arr[i][1])] for i in hull.vertices]
        return {"hull": ordered}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sensors/batch/data")
async def get_sensor_batch_data(sensor_ids: str, date_from: datetime, date_to: datetime, db: AsyncSession = Depends(get_db)):
    """
    Returns time-series data for one or more sensors, automatically bucketed
    via TimescaleDB's time_bucket() based on the requested date range.
    """
    try:
        # THE FIX: Force the requested date_to to the absolute end of the day
        date_to_end = date_to.replace(hour=23, minute=59, second=59)

        sid_list = [s.strip().lower() for s in sensor_ids.split(',')]

        # Dynamic time bucketing based on range length — keeps the response
        # payload a manageable size regardless of how wide the date range is.
        delta_days = (date_to_end.date() - date_from.date()).days
        if delta_days <= 3:
            bucket = "15 minutes"  # Exact readings (raw cadence)
        elif delta_days <= 14:
            bucket = "1 hour"      # Hourly trends
        else:
            bucket = "1 day"       # Daily averages to keep the payload light

        result = await db.execute(text(f"""
            SELECT
                sensor_id,
                time_bucket('{bucket}', date_time) AS bucketed_time,
                AVG(soil_temperature) AS avg_temp,
                AVG(watermark) AS avg_watermark
            FROM agricsensors
            WHERE LOWER(sensor_id) = ANY(:sids)
              AND date_time >= :t1 AND date_time <= :t2
            GROUP BY sensor_id, bucketed_time
            ORDER BY bucketed_time ASC
        """), {"sids": sid_list, "t1": date_from, "t2": date_to_end})

        rows = result.fetchall()

        # Format the data for Plotly: { "FF-01": {"t": [], "temp": [], "watermark": []} }
        output = {sid.upper(): {"t": [], "temp": [], "watermark": []} for sid in sid_list}

        for r in rows:
            sid = r.sensor_id.upper()
            if sid in output:
                output[sid]["t"].append(r.bucketed_time.isoformat())
                output[sid]["temp"].append(round(r.avg_temp, 2) if r.avg_temp is not None else None)
                output[sid]["watermark"].append(round(r.avg_watermark, 2) if r.avg_watermark is not None else None)

        return output
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sensors/summary")
async def sensors_summary(date_from: date = Query(default=date(2022, 1, 1)), date_to: date = Query(default=date.today()), db: AsyncSession = Depends(get_db)):
    """Return every sensor with its coordinates and averaged readings over
    the given range (LEFT JOIN so sensors with no data still appear)."""
    result = await db.execute(text("""
        SELECT c.sensor_id as id, c.latitude as lat, c.longitude as lon,
               AVG(a.soil_temperature) as temp, AVG(a.watermark) as watermark
        FROM sensor_coordinates c
        LEFT JOIN agricsensors a ON c.sensor_id = a.sensor_id AND a.date_time BETWEEN :t1 AND :t2
        WHERE c.latitude IS NOT NULL AND c.longitude IS NOT NULL
        GROUP BY c.sensor_id, c.latitude, c.longitude
    """), {"t1": date_from, "t2": date_to})
    return [dict(r._mapping) for r in result]


@app.post("/slice/compute")
async def compute_slice(req: SliceRequest, date_from: date = Query(default=date(2022, 1, 1)), date_to: date = Query(default=date.today()), db: AsyncSession = Depends(get_db)):
    """Compute an interpolated cross-section along the path of sensor IDs
    / waypoints given in the request body. Powers the frontend's slice tool."""
    try:
        path_len, delaunay_len, distances, temp_list, wm_list = await get_slice_data([sid.lower() for sid in req.sensor_ids], date_from, date_to, db)
        return {"sensors_used": path_len, "distance_axis": distances, "temperature": temp_list, "watermark": wm_list, "delaunay_nodes": delaunay_len}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- SINGLE SENSOR EXPORT ---

@app.get("/export/csv")
async def export_sensor_csv(sensor_id: str, date_from: date = Query(default=date(2022, 1, 1)), date_to: date = Query(default=date.today()), db: AsyncSession = Depends(get_db)):
    """Export raw readings for a single sensor as a downloadable CSV file."""
    try:
        result = await db.execute(text("SELECT date_time, soil_temperature, watermark FROM agricsensors WHERE sensor_id = :sid AND date_time BETWEEN :t1 AND :t2 ORDER BY date_time ASC"), {"sid": sensor_id.lower(), "t1": date_from, "t2": date_to})
        rows = result.fetchall()
        stream = io.StringIO()
        csv_writer = csv.writer(stream)
        csv_writer.writerow(["Timestamp", "Sensor ID", "Soil Temperature (°C)", "Watermark (cb)"])
        for r in rows: csv_writer.writerow([r.date_time, sensor_id.upper(), r.soil_temperature, r.watermark])
        return Response(content=stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=sensor_{sensor_id.lower()}_raw.csv"})
    except Exception as e:
        return Response(content=f"CSV Export Error: {str(e)}", status_code=500)


@app.get("/export/batch/csv")
async def export_batch_csv(sensor_ids: str, date_from: date = Query(default=date(2022, 1, 1)), date_to: date = Query(default=date.today()), db: AsyncSession = Depends(get_db)):
    """Multi-sensor CSV counterpart to /export/batch/pdf — one row per
    (sensor, timestamp) reading, covering exactly the sensor set the
    frontend's comparative Sensor Profile chart was built from."""
    try:
        sids = [s.strip().lower() for s in sensor_ids.split(',') if s.strip()]
        if not sids:
            return Response(content="No sensors selected.", status_code=400)

        # Strict datetime conversion for asyncpg — Query params come in as
        # `date` objects, so pin them to the very start/end of each day.
        query_date_from = datetime(date_from.year, date_from.month, date_from.day, 0, 0, 0)
        query_date_to = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)

        result = await db.execute(text("""
            SELECT sensor_id, date_time, soil_temperature, watermark
            FROM agricsensors
            WHERE LOWER(sensor_id) = ANY(:sids)
              AND date_time >= :t1 AND date_time <= :t2
            ORDER BY sensor_id, date_time ASC
        """), {"sids": sids, "t1": query_date_from, "t2": query_date_to})

        rows = result.fetchall()
        if not rows:
            return Response(content="No data found.", status_code=404)

        stream = io.StringIO()
        csv_writer = csv.writer(stream)
        csv_writer.writerow(["Timestamp", "Sensor ID", "Soil Temperature (°C)", "Watermark (cb)"])
        for r in rows:
            csv_writer.writerow([r.date_time, r.sensor_id.upper(), r.soil_temperature, r.watermark])

        return Response(content=stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=comparative_export.csv"})
    except Exception as e:
        return Response(content=f"Batch CSV Export Error: {str(e)}", status_code=500)


@app.get("/export/pdf")
async def export_sensor_pdf(sensor_id: str, date_from: date = Query(default=date(2025, 1, 1)), date_to: date = Query(default=date.today()), preview: bool = False, db: AsyncSession = Depends(get_db)):
    """Export a single-sensor readings chart as a one-page PDF report.
    `preview=True` sets the response to render inline in the browser
    instead of triggering a file download."""
    try:
        result = await db.execute(text("SELECT date_time, soil_temperature, watermark FROM agricsensors WHERE sensor_id = :sid AND date_time BETWEEN :t1 AND :t2"), {"sid": sensor_id.lower(), "t1": date_from, "t2": date_to})
        rows = result.fetchall()
        if not rows: return Response(content="No data found.", status_code=404)

        dates, temps, watermarks = [r.date_time for r in rows], [r.soil_temperature for r in rows if r.soil_temperature is not None], [r.watermark for r in rows if r.watermark is not None]
        # Hold the PDF lock while generating the chart + building the PDF,
        # since matplotlib and the temp-file handling aren't safe for
        # fully concurrent use across simultaneous export requests.
        async with pdf_lock:
            chart_buffer = generate_sensor_chart(dates, temps, watermarks, sensor_id)

            def build_pdf(image_path):
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("helvetica", size=12)
                pdf.cell(0, 8, f"Devil's Bridge Network - Sensor Profile", ln=True)
                pdf.set_font("helvetica", size=10)
                pdf.cell(0, 6, f"Target Sensor: {sensor_id.upper()}", ln=True)
                pdf.cell(0, 6, f"Date Range: {date_from} to {date_to}", ln=True)
                pdf.image(image_path, x=10, w=190, y=40)
                return pdf

            pdf_bytes = render_pdf_with_chart(chart_buffer, build_pdf)
        disp = "inline" if preview else "attachment"
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f"{disp}; filename={sensor_id}_report.pdf"})
    except Exception as e:
        traceback.print_exc()  # full traceback in the server console — the returned message alone often hides the real cause
        return Response(content=str(e), status_code=500)


@app.get("/export/batch/pdf")
async def export_batch_pdf(sensor_ids: str, date_from: date = Query(...), date_to: date = Query(...), preview: bool = False, db: AsyncSession = Depends(get_db)):
    """Export a landscape PDF report containing the overlaid comparative
    chart for multiple sensors at once."""
    try:
        # Safely parse IDs, ignoring empty strings
        sids = [s.strip().lower() for s in sensor_ids.split(',') if s.strip()]
        if not sids:
            return Response(content="No sensors selected.", status_code=400)

        # Strict datetime conversion for asyncpg
        query_date_from = datetime(date_from.year, date_from.month, date_from.day, 0, 0, 0)
        query_date_to = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)

        result = await db.execute(text("""
            SELECT sensor_id, date_time, soil_temperature, watermark 
            FROM agricsensors 
            WHERE LOWER(sensor_id) = ANY(:sids) 
              AND date_time >= :t1 AND date_time <= :t2
            ORDER BY sensor_id, date_time ASC
        """), {"sids": sids, "t1": query_date_from, "t2": query_date_to})

        rows = result.fetchall()
        if not rows:
            return Response(content="No data found.", status_code=404)

        # Group rows by sensor so each series can be plotted separately on
        # the comparative chart; NaN placeholders keep gaps visually honest
        # rather than silently interpolating across missing readings.
        data_by_sensor = {sid: {"dates": [], "temps": [], "wms": []} for sid in sids}
        for r in rows:
            sid = r.sensor_id.lower()
            if sid in data_by_sensor:
                data_by_sensor[sid]["dates"].append(r.date_time)
                data_by_sensor[sid]["temps"].append(r.soil_temperature if r.soil_temperature is not None else float('nan'))
                data_by_sensor[sid]["wms"].append(r.watermark if r.watermark is not None else float('nan'))

        async with pdf_lock:
            chart_buffer = generate_comparative_chart(data_by_sensor, sids)
            tmp_path = os.path.join(tempfile.gettempdir(), f"chart_{uuid.uuid4().hex}.png")
            with open(tmp_path, "wb") as f:
                f.write(chart_buffer.getbuffer())

            try:
                pdf = FPDF(orientation='L')  # landscape — comparative chart is wide
                pdf.add_page()
                pdf.set_font("helvetica", "B", 16)
                pdf.cell(0, 10, "Devil's Bridge Network - Comparative Analysis", ln=True, align="C")
                pdf.set_font("helvetica", "", 12)
                pdf.cell(0, 8, f"Date Range: {date_from} to {date_to}  |  Sensors: {', '.join([s.upper() for s in sids])}", ln=True, align="C")
                pdf.image(tmp_path, x=10, w=270, y=35)

                output = pdf.output()
                pdf_bytes = bytes(output) if not hasattr(output, 'encode') else output.encode('latin-1')
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        # THE FIX: Tell the browser to render inline if preview=true
        disp = "inline" if preview else "attachment"
        return Response(
            content=pdf_bytes, 
            media_type="application/pdf", 
            headers={"Content-Disposition": f"{disp}; filename=comparative_report.pdf"}
        )
    except Exception as e:
        traceback.print_exc()  # full traceback in the server console — the returned message alone often hides the real cause
        return Response(content=f"Batch Export Error: {str(e)}", status_code=500)


# --- NEW: SLICE EXPORTS ---

@app.get("/export/slice/csv")
async def export_slice_csv(sensor_ids: str, date_from: date = Query(default=date(2025, 1, 1)), date_to: date = Query(default=date.today()), db: AsyncSession = Depends(get_db)):
    """Export the interpolated cross-section (distance vs. temp/watermark)
    for a given path of sensor IDs / waypoints as a CSV file."""
    try:
        ids = [s.strip().lower() for s in sensor_ids.split(',')]
        _, _, distances, temps, wms = await get_slice_data(ids, date_from, date_to, db)

        stream = io.StringIO()
        csv_writer = csv.writer(stream)
        csv_writer.writerow(["Distance_m", "Interpolated_Temp_C", "Interpolated_Watermark_cb"])
        for d, t, w in zip(distances, temps, wms):
            csv_writer.writerow([round(d, 2), round(t, 2) if t else '', round(w, 2) if w else ''])

        return Response(content=stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=slice_export.csv"})
    except Exception as e:
        return Response(content=f"Slice CSV Error: {str(e)}", status_code=500)


@app.get("/export/slice/pdf")
async def export_slice_pdf(sensor_ids: str, date_from: date = Query(default=date(2025, 1, 1)), date_to: date = Query(default=date.today()), preview: bool = False, db: AsyncSession = Depends(get_db)):
    """Export the interpolated cross-section chart for a given path of
    sensor IDs / waypoints as a one-page PDF report."""
    try:
        ids = [s.strip().lower() for s in sensor_ids.split(',')]
        _, _, distances, temps, wms = await get_slice_data(ids, date_from, date_to, db)

        valid_temps, valid_wms = [t for t in temps if t is not None], [w for w in wms if w is not None]
        avg_t = sum(valid_temps) / len(valid_temps) if valid_temps else 0

        async with pdf_lock:
            chart_buffer = generate_slice_chart(distances, temps, wms, sensor_ids.upper())

            def build_pdf(image_path):
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("helvetica", size=12)
                pdf.cell(0, 8, f"Devil's Bridge Network - Topological Slice Report", ln=True)
                pdf.set_font("helvetica", size=10)
                pdf.cell(0, 6, f"Cross-Section Path: {sensor_ids.upper()}", ln=True)
                pdf.cell(0, 6, f"Date Range: {date_from} to {date_to}", ln=True)
                pdf.cell(0, 6, f"Total Path Distance: {distances[-1]:.2f}m" if distances else "0m", ln=True)
                pdf.image(image_path, x=10, w=190, y=45)
                return pdf

            pdf_bytes = render_pdf_with_chart(chart_buffer, build_pdf)
        disp = "inline" if preview else "attachment"
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f"{disp}; filename=slice_report.pdf"})
    except Exception as e:
        traceback.print_exc()  # full traceback in the server console — the returned message alone often hides the real cause
        return Response(content=str(e), status_code=500)
