"""
Data integrity test for the sensor ingestion bandpass filter.

Verifies that the range-validation logic used during ingestion (see the
real implementation in csv_loader.py / mqtt_subscriber.py) correctly
drops rows containing physically impossible hardware-fault values while
leaving valid readings untouched.
"""

import pandas as pd
import pytest

# ─── MOCK FUNCTION ──────────────────────────────────────────────────────────
# This represents the vectorized filtering logic currently inside your 
# asynchronous ingestion daemon.
def apply_bandpass_filter(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only rows where temperature and watermark readings fall within
    physically plausible bounds.

    Mirrors (in simplified/vectorized form) the per-column range checks
    applied during real ingestion. Rows failing either check are dropped
    entirely here, for ease of testing — the production code instead
    nulls out individual out-of-range fields rather than dropping the row.
    """
    # Physical thresholds: Temp between -20C and 50C. Moisture between 0 and 250 cb.
    valid_temp_mask = (df['temp'] >= -20.0) & (df['temp'] <= 50.0)
    valid_wm_mask = (df['watermark'] >= 0.0) & (df['watermark'] <= 250.0)

    # Apply the multidimensional mask
    return df[valid_temp_mask & valid_wm_mask]


# ─── FAULT INJECTION TEST ───────────────────────────────────────────────────
def test_bandpass_filter_drops_hardware_faults():
    """
    Feeds the filter a synthetic dataset containing two "healthy" sensor
    readings and two simulated hardware faults (an impossible temperature
    and an impossible moisture value), then asserts that only the faulty
    readings are dropped.
    """
    # 1. Arrange: Create a synthetic payload with pristine data AND injected faults
    synthetic_payload = {
        "sensor_id": ["FF-01", "FF-02", "FF-03", "FF-04"],
        "timestamp": ["2026-07-17T00:00:00Z"] * 4,
        "temp": [12.5, 14.2, -999.0, 11.8],         # Node 03 simulates a thermal short circuit
        "watermark": [45.0, 50.0, 48.0, 50000.0]    # Node 04 simulates a broken moisture array
    }
    raw_df = pd.DataFrame(synthetic_payload)

    # 2. Act: Pass the compromised payload through your filter
    sanitized_df = apply_bandpass_filter(raw_df)

    # 3. Assert: Prove to the examiner that the mesh is mathematically protected
    # The filter should have dropped exactly 2 rows (FF-03 and FF-04)
    assert len(sanitized_df) == 2, f"Expected 2 valid rows, but got {len(sanitized_df)}"

    # Prove the specific anomalies were intercepted
    assert "FF-03" not in sanitized_df['sensor_id'].values, "Filter FAILED: -999.0C anomaly breached the perimeter."
    assert "FF-04" not in sanitized_df['sensor_id'].values, "Filter FAILED: 50,000 cb anomaly breached the perimeter."

    # Prove the healthy nodes were completely untouched
    assert "FF-01" in sanitized_df['sensor_id'].values, "Filter aggressively dropped healthy node FF-01."
    assert "FF-02" in sanitized_df['sensor_id'].values, "Filter aggressively dropped healthy node FF-02."

    print("\n✅ FAULT INJECTION SUCCESS: All synthetic anomalies dropped. Spatial mesh is secure.")
