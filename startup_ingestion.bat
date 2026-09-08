@echo off
:: Give Docker 120 seconds to wake up the database after login
timeout /t 120 /nobreak

:: Navigate to your project folder
cd C:\Users\danso\project\backend

:: Activate the virtual environment
call .\venv\Scripts\activate

:: Run the ingestion script
python auto_ingest_daemon.py