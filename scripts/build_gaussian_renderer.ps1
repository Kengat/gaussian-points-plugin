param(
  [string]$Configuration = "Release",
  [string]$Platform = "x64"
)

$ErrorActionPreference = "Stop"

$buildNative = Join-Path $PSScriptRoot "build_native.ps1"
if (-not (Test-Path $buildNative)) {
  throw "Missing build_native.ps1 next to build_gaussian_renderer.ps1"
}

powershell -ExecutionPolicy Bypass -File $buildNative -Target gaussian -Configuration $Configuration -Platform $Platform
exit $LASTEXITCODE
