-- ============================================================================
-- AgriSense database schema
--
-- Sets up TimescaleDB + PostGIS extensions, the static sensor-location
-- table, a bookkeeping table used by the CSV ingestion pipeline to avoid
-- re-processing files, and the main sensor-reading hypertable with
-- compression enabled for older data.
-- ============================================================================

-- 1. Enable Required Extensions
-- timescaledb: turns agricsensors into an efficient time-partitioned table
-- postgis: enables geometry columns / spatial queries on sensor locations
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2. Create Metadata & Tracking Tables

-- Static location + status info for each physical sensor node.
CREATE TABLE IF NOT EXISTS sensor_coordinates (
    sensor_id TEXT PRIMARY KEY,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    altitude DOUBLE PRECISION NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);

-- Tracks which CSV files have already been ingested, so the batch loader
-- (csv_loader.py) can skip files it has already processed.
CREATE TABLE IF NOT EXISTS processed_files (
    filename TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Create the Base Time-Series Table (Fixed Primary Key)
-- Raw sensor readings. Composite primary key (sensor_id, date_time) means
-- inserting the same reading twice is naturally deduplicated via
-- ON CONFLICT DO NOTHING in the ingestion scripts.
CREATE TABLE IF NOT EXISTS agricsensors (
    sensor_id TEXT NOT NULL REFERENCES sensor_coordinates(sensor_id),
    date_time TIMESTAMPTZ NOT NULL,
    light_intensity DOUBLE PRECISION,
    soil_temperature DOUBLE PRECISION,
    watermark_frequency DOUBLE PRECISION,
    watermark DOUBLE PRECISION,
    PRIMARY KEY (sensor_id, date_time)
);

-- 4. Convert Base Table to a TimescaleDB Hypertable
-- Partitions agricsensors into monthly chunks by date_time for faster
-- time-range queries and easier retention/compression management.
SELECT create_hypertable('agricsensors', 'date_time',
    chunk_time_interval => INTERVAL '1 month',
    migrate_data => true,
    if_not_exists => true
);

-- 5. Enable and Configure Compression
-- Compress chunks segmented by sensor_id (keeps rows for the same sensor
-- together, improving compression ratio for per-sensor time-series data).
ALTER TABLE agricsensors SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sensor_id'
);
-- Automatically compress any chunk older than 6 months.
SELECT add_compression_policy('agricsensors', INTERVAL '6 months');

-- Redundant safety-net call: re-asserts the hypertable exists (no-op if
-- step 4 already ran successfully).
SELECT create_hypertable('agricsensors', 'date_time', if_not_exists => TRUE);
