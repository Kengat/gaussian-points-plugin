param(
  [ValidateSet("all", "gaussian", "pointcloud", "bridge")]
  [string[]]$Target = @("all"),
  [string]$Configuration = "Release",
  [string]$Platform = "x64"
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "scripts\build_native.ps1"
if (-not (Test-Path $scriptPath)) {
  throw "Missing scripts\build_native.ps1"
}

powershell -ExecutionPolicy Bypass -File $scriptPath -Target $Target -Configuration $Configuration -Platform $Platform
exit $LASTEXITCODE
