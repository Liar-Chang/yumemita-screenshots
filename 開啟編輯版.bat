@echo off
chcp 65001 >nul
cd /d "%~dp0site"

echo === Starting local edit server ===
echo Browser will open automatically in a few seconds.
echo Keep this window open while editing - closing it stops the local server.
echo.
npm run dev
