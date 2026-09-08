"""
Auto-ingest daemon for AgriSense.

Watches a shared network folder for new/changed CSV files and, whenever
one shows up, runs csv_loader.py to pull it into the database. Also
performs a one-off "catch-up" scan on startup in case files arrived
while the daemon wasn't running.

Run with: `python auto_ingest_daemon.py` (intended to run continuously,
e.g. as a background service on the machine with access to the network
share).
"""

import time
import os
import sys
import subprocess
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Resolve paths relative to this script's own directory so the daemon works
# correctly regardless of the working directory it is launched from.
SCRIPT_DIR    = Path(__file__).resolve().parent
LOADER_SCRIPT = SCRIPT_DIR / "csv_loader.py"


class CSVMonitorHandler(FileSystemEventHandler):
    """
    Watchdog event handler that reacts to filesystem changes in the
    watched folder by re-running the CSV loader whenever a relevant
    CSV file appears.
    """

    def process_event(self, event):
        """
        Shared handler for create/move/modify events: filters out
        anything that isn't a CSV file, waits briefly to let network
        file writes finish, then triggers csv_loader.py as a subprocess.
        """
        # Ignore directories
        if event.is_directory:
            return

        # Check if the file that triggered the event is a CSV
        if event.src_path.lower().endswith(".csv") or \
           (hasattr(event, 'dest_path') and event.dest_path.lower().endswith(".csv")):

            print(f"\n🚨 Network Drop Detected! Triggering ingestion...")

            # Add a small delay. Network drives often lock the file for a split second 
            # while finishing the sync. This prevents "File in Use" crashes.
            time.sleep(2)

            try:
                # Trigger the loader (uses the virtual environment's python securely)
                subprocess.run([sys.executable, str(LOADER_SCRIPT)], check=True)
                print("✅ Auto-ingest cycle complete. Resuming watch...")
            except subprocess.CalledProcessError as e:
                print(f"❌ Error running csv_loader.py: {e}")

    def on_created(self, event):
        """Fires when a new file/folder is created in the watched directory."""
        self.process_event(event)

    def on_moved(self, event):
        """Fires on renames — needed because some cloud-sync clients write
        a temp file first, then rename it to the final .csv filename."""
        self.process_event(event)

    def on_modified(self, event):
        """Fires on file content changes — some network drives create an
        empty file first and only populate it on a later 'modified' event."""
        self.process_event(event)


if __name__ == "__main__":
    # Point directly to the Aberystwyth network folder
    folder_to_watch = r"C:\Users\danso\Aberystwyth University\Fred Labrosse [ffl] (Staff) - agriSensors"

    # ---------------------------------------------------------
    # THE FINAL FIX: Automated "Catch-Up" Sweep
    # Process any files that arrived while the daemon was offline
    # before we start the watchdog loop.
    # ---------------------------------------------------------
    print("🔄 Performing initial catch-up scan for existing files...")
    try:
        subprocess.run([sys.executable, str(LOADER_SCRIPT)], check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Initial scan failed: {e}")
    # ---------------------------------------------------------

    # Set up the watchdog observer to monitor the folder going forward.
    event_handler = CSVMonitorHandler()
    observer = Observer()
    observer.schedule(event_handler, path=folder_to_watch, recursive=False)

    observer.start()
    print(f"👁️ Watchdog active. Monitoring network directory '{folder_to_watch}'...")

    # Keep the main thread alive so the observer's background thread keeps
    # running; Ctrl+C stops the daemon cleanly.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("Watchdog manually terminated.")

    observer.join()
