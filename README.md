AgriSense — Devil's Bridge Soil Sensor Network





> A full-stack geospatial visualisation platform for 20 Tektelic Kiwi IoT soil sensors deployed at Devil's Bridge, Wales.



Live Repo: `https://github.com/bed78/AgriSense\_MSc\_Dissertation`



!\[Python](https://img.shields.io/badge/Python-3.11%2B-blue)

!\[FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)

!\[React](https://img.shields.io/badge/React-Leaflet-blue)

!\[PostgreSQL](https://img.shields.io/badge/PostgreSQL-TimescaleDB%20%2B%20PostGIS-blue)



Overview

AgriSense ingests soil telemetry (temperature + Watermark moisture), stores it in a TimescaleDB/PostGIS-backed PostgreSQL database, serves it via a FastAPI backend, and visualises it on an interactive React + Leaflet + Plotly frontend.



**Key Features:**

\- Interactive Leaflet map with Delaunay triangulation \& barycentric interpolation

\- IDW hover readout and gradient-filled mesh rendering

\- Timeline scrubber, transect / cross-section slicing

\- Sensor health monitoring (online/offline status for 20 nodes)

\- PDF/CSV Reports \& Export Center

\- Idempotent auto-ingestion daemon (watchdog) for daily CSVs



**Architecture**

Four decoupled layers:

1\.  Persistence: PostgreSQL 16 + TimescaleDB (Docker `chm9360db:5433`) + PostGIS

2\.  Application: FastAPI (async) — serves geometry, telemetry, transect, reports

3\.  Presentation: React (Vite) + Leaflet + Plotly

4\.  Ingestion: `csv\_loader.py` + `auto\_ingest\_daemon.py` + `processed\_files` ledger



\*\*Database Tables:\*\*

\- `agricsensors` — hypertable of sensor readings

\- `sensor\_coordinates` — lat/long/altitude per node

\- `processed\_files` — ingestion ledger for deduplication



**Tech Stack**

\- Backend: Python 3.11+, FastAPI, Uvicorn, SQLAlchemy, SciPy, Watchdog

\- Frontend: Node.js 20, React, Vite, Leaflet, Plotly

\- DB: Docker, TimescaleDB, PostGIS

\- Testing: Locust, custom evaluation scripts



**Project Structure**

├── conf.json                 # local config (not committed)

├── backend/

│   ├── main.py               # FastAPI app \& routes

│   ├── database.py           # async DB engine

│   ├── Schema.sql            # full DB schema

│   ├── sync\_map.py           # loads static 20-node map

│   ├── csv\_loader.py         # bulk CSV ingestion

│   ├── auto\_ingest\_daemon.py # watchdog daemon

│   └── requirements.txt

└── frontend/

&#x20;   ├── src/components/

&#x20;   │   ├── SensorMap.jsx

&#x20;   │   ├── SensorChart.jsx

&#x20;   │   └── SliceChart.jsx

&#x20;   └── package.json





**Quick Start**



**1. Database**

*bash*



docker run --name chm9360db -e POSTGRES\_PASSWORD=postgres -p 5433:5432 -v chm9360db\_data:/var/lib/postgresql/data -d timescale/timescaledb-ha:pg15

docker cp backend/Schema.sql chm9360db:/Schema.sql

docker exec -it chm9360db psql -U postgres -d postgres -f /Schema.sql





**2. Backend**

*bash*



cd backend

python -m venv venv

.\\venv\\Scripts\\activate

pip install -r requirements.txt

python sync\_map.py

python csv\_loader.py

uvicorn main:app --reload

Docs: http://localhost:8000/docs



**3. Daemon (separate terminal)**

python auto\_ingest\_daemon.py



4\. Frontend (separate terminal)

*bash*



cd frontend

npm install

npm run dev

App: http://localhost:5173



**Author**

Danso — MSc Computer Science 
Dissertation Supervisor: Dr. Fred 

Developed for academic evaluation at Devil's Bridge sensor network site.

