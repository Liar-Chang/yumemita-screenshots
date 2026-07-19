@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === Batch importing screenshots ===
python pipeline\batch_add_images.py --folder "%APPDATA%\PotPlayerMini64\Capture"
echo.

echo === Checking for changes to upload ===
git add -A
git diff --cached --quiet
if %errorlevel% equ 0 (
    echo No changes detected, nothing to upload.
) else (
    git commit -m "Batch import manual screenshots"
    echo.
    echo === Uploading to GitHub, site updates in 1-2 min ===
    git push
)

echo.
pause
