import random
from locust import HttpUser, task, between


class GridSensorDashboardUser(HttpUser):
    # Simulate a user waiting between 0.5 to 2 seconds between timeline clicks
    wait_time = between(0.5, 2.0)

    # Point this at wherever your FastAPI backend is actually running.
    # Override with `--host` on the CLI instead if you prefer not to hardcode it.
    host = "http://localhost:8000"

    def on_start(self):
        """
        Simulates the React app mounting once per session and pulling down
        the static geographic network constraints (mesh + convex hull).
        This happens once per user, not repeatedly, matching real usage.
        """
        self._get("/sensors/mesh", "/sensors/mesh [Load Geometry]")
        self._get("/sensors/hull", "/sensors/hull [Load Convex Hull]")

    def _get(self, url, name):
        """Shared GET helper with response validation."""
        with self.client.get(url, name=name, catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Got {resp.status_code}: {resp.text[:200]}")
            else:
                resp.success()

    @task(3)
    def simulate_timeline_scrubbing(self):
        """
        Simulates a researcher scrolling through the historical timeline.
        Generates random dates to force the API to run real, non-cached
        TimescaleDB chunk exclusion queries.
        """
        # Pick a random window from your dataset year (2026).
        # Spans months so chunk-exclusion is exercised across chunk
        # boundaries, not just within a single hypertable chunk.
        random_month = random.randint(1, 12)
        random_day_start = random.randint(1, 20)

        date_from = f"2026-{random_month:02d}-{random_day_start:02d}T00:00:00Z"
        date_to = f"2026-{random_month:02d}-{(random_day_start + 7):02d}T23:59:59Z"

        self._get(
            f"/sensors/timeline?date_from={date_from}&date_to={date_to}",
            "/sensors/timeline [Scrub Timeline]",
        )

    @task(2)
    def view_individual_node_popup(self):
        """
        Simulates a user clicking on a specific CircleMarker to view
        individual telemetry.
        """
        random_node_id = f"ff-{random.randint(1, 20):02d}"
        self._get(f"/sensors?id={random_node_id}", "/sensors [Fetch Node Detail]")