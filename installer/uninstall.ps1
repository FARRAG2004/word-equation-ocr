# Removes the Word add-in, its Python environment and the downloaded model.
$home_ = Join-Path $env:LOCALAPPDATA "EquationAddIn"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "uvicorn app:app" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item "HKCU:\Software\Microsoft\Office\Word\Addins\EquationAddIn", "HKCU:\Software\EquationAddIn" -Recurse -ErrorAction SilentlyContinue
Set-Location $env:TEMP
Remove-Item $home_ -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Equation add-in removed." -ForegroundColor Green
