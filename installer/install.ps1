# One-click installer: Python + OCR model + Word add-in, all per-user (no admin needed).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
$src  = $PSScriptRoot
$home_ = Join-Path $env:LOCALAPPDATA "EquationAddIn"
function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

Step "Checking Microsoft Word"
if (-not (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe" -ErrorAction SilentlyContinue)) {
    throw "Microsoft Word (desktop) is not installed."
}
Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object { throw "Please close Word and run the installer again." }

$update = Test-Path (Join-Path $home_ ".venv\Scripts\python.exe")
if ($update) { Write-Host "Existing installation found: updating the program only (Python, PyTorch and models are kept)." -ForegroundColor Green }

Step "Copying files to $home_"
New-Item -ItemType Directory -Force $home_ | Out-Null
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "uvicorn app:app" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
# program folders are replaced whole, so files removed in a new version do not linger; models and .venv stay
foreach ($d in "server", "addin") { Remove-Item (Join-Path $home_ $d) -Recurse -Force -ErrorAction SilentlyContinue }
Copy-Item "$src\server", "$src\addin", "$src\convert.py", "$src\requirements.txt", "$src\uninstall.ps1", "$src\uninstall.bat" $home_ -Recurse -Force

Step "Finding Python 3.12"
$python = $null
try { $python = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null) } catch { }
if (-not $python) {
    $python = Join-Path $home_ "python\python.exe"
    if (-not (Test-Path $python)) {
        Write-Host "Downloading Python 3.12 from python.org..."
        $exe = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
        Invoke-WebRequest "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" -OutFile $exe
        Start-Process $exe -Wait -ArgumentList "/quiet InstallAllUsers=0 TargetDir=`"$home_\python`" Include_launcher=0 PrependPath=0 Include_test=0 Shortcuts=0 AssociateFiles=0"
    }
}
Write-Host "Python: $python"

Step "Creating virtual environment"
$venvPy = Join-Path $home_ ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $python -m venv (Join-Path $home_ ".venv") }
& $venvPy -m pip install --quiet --upgrade pip

$hasTorch = $false
try { & $venvPy -c "import torch, sys; sys.exit(0 if torch.__version__.startswith('2.6.0') else 1)" 2>$null; $hasTorch = ($LASTEXITCODE -eq 0) } catch { }
if ($hasTorch) {
    Write-Host "PyTorch 2.6.0 already installed."
} else {
Step "Installing PyTorch (this is the big download, 1-3 GB)"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "NVIDIA GPU found: installing CUDA build"
    $index = "https://download.pytorch.org/whl/cu124"
} else {
    Write-Host "No NVIDIA GPU: installing CPU build (slower, still works)"
    $index = "https://download.pytorch.org/whl/cpu"
}
& $venvPy -m pip install --quiet torch==2.6.0 torchvision==0.21.0 --index-url $index
if ($LASTEXITCODE) { throw "PyTorch install failed." }
}

Step "Installing OCR libraries"
& $venvPy -m pip install --quiet -r (Join-Path $home_ "requirements.txt")
if ($LASTEXITCODE) { throw "Library install failed." }

Step "Loading the OCR models (downloaded only if missing) and testing the pipeline"
Push-Location (Join-Path $home_ "server")
& $venvPy -c "import core; core.warm(); print('OMML test:', core.latex_to_omml('E=mc^2')[:30], '...')"
$ok = -not $LASTEXITCODE
Pop-Location
if (-not $ok) { throw "Model/pipeline test failed." }

Step "Checking VSTO runtime"
$vsto = "HKLM:\SOFTWARE\Microsoft\VSTO Runtime Setup\v4R", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VSTO Runtime Setup\v4R"
if (-not ($vsto | Where-Object { Test-Path $_ })) {
    Write-Host "Installing Visual Studio Tools for Office runtime (needs admin approval)..."
    $exe = Join-Path $env:TEMP "vstor_redist.exe"
    Invoke-WebRequest "https://go.microsoft.com/fwlink/?LinkId=140384" -OutFile $exe
    Start-Process $exe -Wait -Verb RunAs -ArgumentList "/q /norestart"
}

Step "Registering the Word add-in"
$manifest = "file:///" + ((Join-Path $home_ "addin\EquationAddIn.vsto") -replace '\\', '/') + "|vstolocal"
$key = "HKCU:\Software\Microsoft\Office\Word\Addins\EquationAddIn"
New-Item -Force $key | Out-Null
Set-ItemProperty $key FriendlyName "Equation Image Converter"
Set-ItemProperty $key Description "Converts equation images to native Word equations (offline)"
Set-ItemProperty $key Manifest $manifest
New-ItemProperty $key LoadBehavior -Value 3 -PropertyType DWord -Force | Out-Null
New-Item -Force "HKCU:\Software\EquationAddIn" | Out-Null
Set-ItemProperty "HKCU:\Software\EquationAddIn" Home $home_

Write-Host "`nDone! Open Word and use the 'Equations' tab." -ForegroundColor Green
Write-Host "The first time, Word may ask whether to install the customization: click Install."
