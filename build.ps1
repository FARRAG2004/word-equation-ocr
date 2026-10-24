# Builds the add-in (Release) and packages dist\EquationAddIn-<version>.zip for a GitHub release.
param([string]$Version = "1.0.0")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# Strict gate: nothing is packaged unless the test suite passes (see tests/run_all.py).
& "$root\.venv\Scripts\python.exe" "$root\tests\run_all.py"
if ($LASTEXITCODE) { throw "Tests failed - not packaging. Fix them, or run tests\run_all.py --update-baseline if a score change is intended." }
$proj = Join-Path $root "addin\EquationAddIn"

# Manifests must be signed; make a self-signed key on first build (never committed).
$pfx = Join-Path $proj "EquationAddIn_Key.pfx"
if (-not (Test-Path $pfx)) {
    $c = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=EquationAddIn" -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(10)
    Export-PfxCertificate -Cert $c -FilePath $pfx -Password (New-Object System.Security.SecureString) | Out-Null
}
$thumb = (Get-PfxData $pfx).EndEntityCertificates[0].Thumbprint

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$msbuild = & $vswhere -latest -requires Microsoft.VisualStudio.Workload.Office -find "MSBuild\**\Bin\MSBuild.exe" | Select-Object -First 1
& $msbuild "$proj\EquationAddIn.csproj" /p:Configuration=Release /p:ManifestCertificateThumbprint=$thumb /v:minimal /nologo
if ($LASTEXITCODE) { throw "Build failed." }

$stage = Join-Path $root "dist\EquationAddIn-$Version"
Remove-Item $stage, "$stage.zip" -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\addin", "$stage\server" | Out-Null
Copy-Item "$proj\bin\Release\*" "$stage\addin" -Exclude *.pdb
Copy-Item "$root\server\*" "$stage\server" -Recurse -Exclude "__pycache__", "*.pyc"
Get-ChildItem "$stage\server" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item "$root\convert.py", "$root\requirements.txt", "$root\installer\*" $stage
Compress-Archive "$stage\*" "$stage.zip"
Write-Host "Packaged $stage.zip"
