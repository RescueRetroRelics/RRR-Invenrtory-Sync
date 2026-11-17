@echo off
rem Hourly store upkeep (replaces the Flow and Alt Text King apps).
rem 1) New Finds: tag new listings "new item", untag after 14 days.
rem 2) Alt text: describe any product photos that have none.
rem Windows Task Scheduler calls this every hour. Safe to run as often as you like.
cd /d "%~dp0"
if not exist data mkdir data
python -m rrr_sync new-tags --apply >> data\upkeep_runs.log 2>&1
python -m rrr_sync alt-text --apply --max 100 >> data\upkeep_runs.log 2>&1
