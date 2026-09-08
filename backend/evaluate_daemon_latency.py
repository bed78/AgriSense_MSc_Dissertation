import time
import uuid
import psycopg2
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

#  target directory watched by daemon
DATA_ROOT = Path(r"C:\Users\danso\Aberystwyth University\Fred Labrosse [ffl] (Staff) - agriSensors")
CONFIG_PATH = Path(__file__).resolve().parent / "conf.json"

POLL_INTERVAL_SECONDS = 0.05
TIMEOUT_SECONDS = 30
SENSOR_ID = "S01"


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r") as f:
        return json.load(f)


def connect(config: dict):
    return psycopg2.connect(
        dbname=config["DBNAME"],
        user=config["DBUSER"],
        password=config["PASSWORD"],
        host=config.get("DBHOST", "127.0.0.1"),
        port=config.get("DBPORT", 5433),
    )


def run_latency_test():
    config = load_config(CONFIG_PATH)

    # Unique filename/timestamp per run so repeated or concurrent runs
    # can't collide with leftover rows from a previous test.
    run_id = uuid.uuid4().hex[:8]
    test_filename = f"latency_test_payload_{run_id}.csv"
    test_file = DATA_ROOT / test_filename
    row_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = connect(config)
    try:
        conn.autocommit = False
        with conn.cursor() as cursor:
            log.info("--- Starting Daemon Latency Test ---")

            # 1. Write dummy file and start stopwatch
            start_time = time.monotonic()
            try:
                with open(test_file, "w") as f:
                    f.write(f"{SENSOR_ID},{row_timestamp},500,22.5,100,50\n")
            except OSError as e:
                log.error(f"Could not write test payload to {test_file}: {e}")
                return

            log.info(f"Test payload written to {test_file}. Waiting for daemon to intercept...")

            # 2. Poll the processed_files table to track the ingestion trigger
            latency = None
            try:
                while True:
                    cursor.execute(
                        "SELECT processed_at FROM processed_files WHERE filename = %s",
                        (test_filename,),
                    )
                    result = cursor.fetchone()

                    if result:
                        latency = time.monotonic() - start_time
                        log.info(f"Interception successful! Total detection latency: {latency:.3f} seconds")

                        # Clean up tracking table and hypertable
                        cursor.execute(
                            "DELETE FROM processed_files WHERE filename = %s",
                            (test_filename,),
                        )
                        cursor.execute(
                            "DELETE FROM agricsensors WHERE date_time = %s AND sensor_id = %s",
                            (row_timestamp, SENSOR_ID),
                        )
                        conn.commit()
                        break

                    if (time.monotonic() - start_time) > TIMEOUT_SECONDS:
                        log.warning(f"Timeout: daemon did not register the file within {TIMEOUT_SECONDS} seconds.")
                        conn.rollback()
                        break

                    time.sleep(POLL_INTERVAL_SECONDS)
            finally:
                # Always remove the test file, whether we succeeded, timed out, or hit an error.
                if test_file.exists():
                    test_file.unlink()

            return latency
    finally:
        conn.close()


if __name__ == "__main__":
    run_latency_test()
