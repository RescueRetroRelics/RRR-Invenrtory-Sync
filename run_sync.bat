@echo off
rem Runs one sync. Windows Task Scheduler calls this every 15 minutes.
cd /d "%~dp0"
if not exist data mkdir data
python -m rrr_sync sync --apply >> data\sync_runs.log 2>&1
