param(
    [string]$Name = "EDTT-Definer-v0.6.2-dev"
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found. Create .\venv or use the development workspace venv."
}
$PreviousExecutableName = $env:EDTT_EXE_NAME

try {
    $env:EDTT_EXE_NAME = $Name
    & $Python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "edtt_definer.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }
}
finally {
    if ($null -eq $PreviousExecutableName) {
        Remove-Item Env:EDTT_EXE_NAME -ErrorAction SilentlyContinue
    }
    else {
        $env:EDTT_EXE_NAME = $PreviousExecutableName
    }
}

Write-Host "Built dist\$Name.exe"
