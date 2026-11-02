@echo off
rem Double-click to audit rescueretrorelics.com for SEO problems and broken links.
rem Read-only: it only looks at pages, it never changes anything on your store.
cd /d "%~dp0"
python -m rrr_audit %*
pause
