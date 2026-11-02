@echo off
rem Sale tier report: double-click this file. Read-only, changes nothing on Shopify or eBay.
cd /d "%~dp0"
if not exist data\sale_analyzer_inputs mkdir data\sale_analyzer_inputs
python -m sale_analyzer
if exist data\sale_analyzer\sale_report.html start "" data\sale_analyzer\sale_report.html
pause
