param(
    [string]$Name = "EDTT-Definer-v0.7.0"
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path (Split-Path $PSScriptRoot -Parent) "venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found. Create .\venv or use the development workspace venv."
}
$PreviousExecutableName = $env:EDTT_EXE_NAME
$PreviousPythonPath = $env:PYTHONPATH
$SourcePath = Join-Path $PSScriptRoot "src"

try {
    $env:EDTT_EXE_NAME = $Name
    $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($PreviousPythonPath)) {
        $SourcePath
    }
    else {
        "$SourcePath$([IO.Path]::PathSeparator)$PreviousPythonPath"
    }
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
    if ($null -eq $PreviousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $PreviousPythonPath
    }
}

Write-Host "Built dist\$Name.exe"
