# Builds the Windows helper: build\dist\MotionRadar\MotionRadar.exe and
# build\MotionRadar-windows.zip. Run from anywhere; expects the Python environment
# with the package and PyInstaller installed, and the dashboard built in web\dist.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if (-not (Test-Path "web\dist\index.html")) { throw "Build the dashboard first: npm --prefix web run build" }

python -m PyInstaller "packaging\motion_radar.py" `
  --name MotionRadar --onedir --console --noconfirm --clean `
  --distpath "build\dist" --workpath "build\work" --specpath "build" `
  --add-data "$root\web\dist;web\dist" `
  --collect-submodules uvicorn --collect-submodules websockets `
  --exclude-module matplotlib --exclude-module sklearn --exclude-module xgboost `
  --exclude-module IPython --exclude-module tkinter --exclude-module PyQt5 `
  --exclude-module PySide6 --exclude-module pytest --exclude-module notebook `
  --exclude-module torch --exclude-module torchvision --exclude-module cv2 `
  --exclude-module numba --exclude-module llvmlite --exclude-module onnxruntime `
  --exclude-module imageio --exclude-module imageio_ffmpeg --exclude-module PIL `
  --exclude-module lxml --exclude-module psycopg2
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$zip = "build\MotionRadar-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "build\dist\MotionRadar" -DestinationPath $zip
"{0:N0} MB  {1}" -f ((Get-Item $zip).Length / 1MB), $zip
