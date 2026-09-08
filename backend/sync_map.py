"""
Sensor coordinate synchronization script.

Reads a master locations CSV (sensorID, latitude, longitude, altitude)
and upserts those coordinates into the `sensor_coordinates` table, so
the map/API always reflects the latest known sensor positions.

Run manually whenever locations.csv is updated: `python sync_map.py`
"""

import os
import json
import asyncio
import asyncpg
import pandas as pd
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sync_map")

# Target the master coordinates file
LOCATIONS_FILE = Path(r"C:\Users\danso\project\locations.csv")


async def main():
    """
    Entry point: load locations.csv, then upsert every row into
    sensor_coordinates (insert new sensors, update coordinates for
    sensors that already exist).
    """
    log.info("🗺️ Starting Sensor Coordinate Synchronization...")

    if not LOCATIONS_FILE.exists():
        log.error(f"✘ Could not find locations file at {LOCATIONS_FILE}")
        return

    try:
        # Load the CSV, specifying the exact column names you provided
        df = pd.read_csv(LOCATIONS_FILE)

        # Extract records, ensuring sensorID is strictly lowercase to match the ingestion pipeline
        records = [
            (str(row.sensorID).lower().strip(), float(row.latitude), float(row.longitude), float(row.altitude))
            for _, row in df.iterrows()
        ]
    except Exception as e:
        log.error(f"✘ Failed to parse {LOCATIONS_FILE.name}: {e}")
        return

    # Prefer conf.json for DB credentials; fall back to an env var if no
    # conf file is present.
    conf_path = Path("conf.json")
    if conf_path.exists():
        with open(conf_path) as f: settings = json.load(f)
        DB_URL = f"postgresql://{settings['DBUSER']}:{settings['PASSWORD']}@{settings.get('DBHOST', 'localhost')}:{settings.get('DBPORT', 5433)}/{settings['DBNAME']}"
    else:
        DB_URL = os.getenv("DATABASE_URL")

    pool = await asyncpg.create_pool(DB_URL)
    try:
        async with pool.acquire() as conn:
            # Upsert logic: Insert new, or update existing coordinates
            await conn.executemany("""
                INSERT INTO sensor_coordinates (sensor_id, latitude, longitude, altitude)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (sensor_id) DO UPDATE SET
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    altitude = EXCLUDED.altitude;
            """, records)

        log.info(f"✅ Successfully synchronized {len(records)} sensor locations to the spatial database!")
    except Exception as e:
        log.error(f"✘ Database insertion error: {e}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
