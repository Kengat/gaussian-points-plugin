param(
  [ValidateSet("all", "gaussian", "pointcloud", "bridge")]
  [string[]]$Target = @("all"),
  [string]$Configuration = "Release",
  [string]$Platform = "x64"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

function Get-MSBuildPath {
  @(
    "C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\amd64\MSBuild.exe",
    "C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Normalize-ProcessPathEnvironment {
  $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
  if (-not $pathValue) {
    $pathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
  }

  [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
  [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}

function Invoke-NativeProjectBuild {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$ProjectPath,
    [Parameter(Mandatory = $true)][string]$DllName,
    [Parameter(Mandatory = $true)][string]$RuntimeDestination
  )

  $msbuild = Get-MSBuildPath
  if (-not $msbuild) {
    throw "MSBuild.exe not found."
  }

  $buildRoot = Join-Path $repoRoot ("build_out\native\" + $Name)
  $outDir = Join-Path $buildRoot "bin\"
  $intDir = Join-Path $buildRoot "obj\"
  $builtDll = Join-Path $outDir $DllName

  New-Item -ItemType Directory -Force -Path $outDir | Out-Null
  New-Item -ItemType Directory -Force -Path $intDir | Out-Null

  Normalize-ProcessPathEnvironment

  & $msbuild $ProjectPath `
    /p:Configuration=$Configuration `
    /p:Platform=$Platform `
    /p:OutDir=$outDir `
    /p:IntDir=$intDir

  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0 -and -not (Test-Path $builtDll)) {
    throw "MSBuild failed for $Name with exit code $exitCode and did not produce $DllName."
  }

  if ($exitCode -ne 0 -and (Test-Path $builtDll)) {
    Write-Warning "MSBuild returned exit code $exitCode for $Name, but $DllName was produced. Continuing."
  }

  if (-not (Test-Path $builtDll)) {
    throw "$Name build finished without $DllName."
  }

  $runtimeDir = Split-Path -Parent $RuntimeDestination
  New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
  Copy-Item -LiteralPath $builtDll -Destination $RuntimeDestination -Force

  [PSCustomObject]@{
    Target = $Name
    Output = $builtDll
    Runtime = $RuntimeDestination
    ExitCode = $exitCode
  }
}

$targetsToBuild =
  if ($Target -contains "all") {
    @("gaussian", "pointcloud", "bridge")
  } else {
    $Target
  }

$projectMap = @{
  gaussian = @{
    ProjectPath = Join-Path $repoRoot "sandbox\cpp\build\GaussianSplatRenderer\GaussianSplatRenderer\GaussianSplatRenderer.vcxproj"
    DllName = "GaussianSplatRenderer.dll"
    RuntimeDestination = Join-Path $repoRoot "sandbox\runtime\GaussianSplatRenderer.dll"
  }
  pointcloud = @{
    ProjectPath = Join-Path $repoRoot "cpp\build\PointCloudRendererDLL\PointCloudRendererDLL\PointCloudRendererDLL.vcxproj"
    DllName = "PointCloudRendererDLL.dll"
    RuntimeDestination = Join-Path $repoRoot "sandbox\runtime\PointCloudRendererDLL.dll"
  }
  bridge = @{
    ProjectPath = Join-Path $repoRoot "sandbox\cpp\build\SketchUpOverlayBridge\SketchUpOverlayBridge\SketchUpOverlayBridge.vcxproj"
    DllName = "SketchUpOverlayBridge.dll"
    RuntimeDestination = Join-Path $repoRoot "sandbox\SketchUpOverlayBridge.dll"
  }
}

$results = @()
foreach ($targetName in $targetsToBuild) {
  $spec = $projectMap[$targetName]
  if (-not $spec) {
    throw "Unknown native target: $targetName"
  }

  $results += Invoke-NativeProjectBuild `
    -Name $targetName `
    -ProjectPath $spec.ProjectPath `
    -DllName $spec.DllName `
    -RuntimeDestination $spec.RuntimeDestination
}

$results | Format-Table -AutoSize
