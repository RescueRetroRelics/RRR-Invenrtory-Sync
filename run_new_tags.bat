@echo off
rem Replaces Shopify Flow: tags new listings "new item" and removes the tag after 14 days.
rem Windows Task Scheduler calls this every hour. Safe to run as often as you like.
cd /d "%~dp0"
if not exist data mkdir data
python -m rrr_sync new-tags --apply >> data\new_tags_runs.log 2>&1
