"""
SQLAlchemy ORM models for the AgriSense database.

These mirror the tables created in Schema.sql:
- SensorReading      -> the `agricsensors` hypertable (raw time-series readings)
- SensorCoordinate   -> the `sensor_coordinates` table (static sensor locations)
"""

from sqlalchemy import Column, String, Float, DateTime, Boolean, Index
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION
from geoalchemy2 import Geometry
from database import Base


class SensorReading(Base):
    """
    One row per sensor reading, keyed by (sensor_id, date_time).

    Maps to the `agricsensors` TimescaleDB hypertable. Values may be
    NULL if a reading failed a validity check during ingestion (see
    csv_loader.py / mqtt_subscriber.py for the range checks applied).
    """
    __tablename__ = "agricsensors"

    sensor_id       = Column(String(10), primary_key=True, index=True)
    date_time       = Column(DateTime(timezone=True), primary_key=True)
    light_intensity = Column(Float)       # lux
    soil_temperature= Column(Float)       # degrees Celsius
    watermark_frequency = Column(Float)   # raw sensor frequency reading
    watermark       = Column(Float)       # soil moisture tension, centibars (cb)


class SensorCoordinate(Base):
    """
    Static metadata for each physical sensor node: where it is and
    whether it's currently considered active/in-service.

    Maps to the `sensor_coordinates` table. `geom` is a PostGIS point
    geometry kept in sync with latitude/longitude for spatial queries.
    """
    __tablename__ = "sensor_coordinates"

    sensor_id  = Column(String(10), primary_key=True)
    latitude   = Column(DOUBLE_PRECISION)
    longitude  = Column(DOUBLE_PRECISION)
    altitude   = Column(Float)
    is_active  = Column(Boolean, default=True)
    geom       = Column(Geometry("POINT", srid=4326), nullable=True)  # SRID 4326 = WGS84 lat/lon
