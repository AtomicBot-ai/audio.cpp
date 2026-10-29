# Lay out a self-contained Windows archive tree (x64 or arm64). Run from the source root.
#
#   -BinDir        a build's output; audiocpp_server.exe, audiocpp_cli.exe, every DLL, and (with
#                  static eSpeak-ng) espeak-ng-data.bin and licenses\espeak-ng\ are copied into
#                  -OutDir, plus LICENSE. Build-only tools (audiocpp_espeak_pack.exe) stay out.
#   -CudaDll       ggml-cuda.dll from a separate (cross) build, added to an existing -OutDir
#   -CudaRoot      CUDA install whose runtime DLLs the binaries need; they are copied in
#   -AllowMissing  extra DLL name patterns that may stay unresolved: provided by a companion
#                  archive (win-cuda12's cudart)
#   -Espeak        fail unless espeak-ng-data.bin and licenses\espeak-ng\COPYING are in -OutDir
#
# Every import of every binary must resolve inside the tree, to an OS DLL, or to an allowed name;
# the NVIDIA driver (nvcuda.dll) is always allowed. The VC++ runtime of -Arch (and its OpenMP
# runtime, vcomp140.dll) is copied in app-locally (Windows on Arm does not ship it; on x64 it is
# merely not guaranteed). Every binary must be a PE image for -Arch.
# Adapted from the stable-diffusion.cpp fork (AtomicBot-ai/stable-diffusion.cpp).
param(
  [string]$BinDir,
  [Parameter(Mandatory)][string]$OutDir,
  [Parameter(Mandatory)][string]$VcVars,
  [ValidateSet('arm64', 'x64')][string]$Arch = 'arm64',
  [string]$CudaDll,
  [string]$CudaRoot,
  [string[]]$AllowMissing = @(),
  [switch]$Espeak,
  [string]$CheckSm
)
$ErrorActionPreference = 'Stop'

$vsRoot = (Resolve-Path (Join-Path (Split-Path $VcVars) '..\..\..')).Path
# Any host's dumpbin reads any machine type; prefer the one native to this runner (windows-11-arm
# may carry only the ARM64-hosted tools).
$hostDir = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'HostARM64\arm64' } else { 'Hostx64\x64' }
$dumpbin = @(Get-ChildItem "$vsRoot\VC\Tools\MSVC\*\bin\$hostDir\dumpbin.exe" -ErrorAction SilentlyContinue) +
  @(Get-ChildItem "$vsRoot\VC\Tools\MSVC\*\bin\Host*\*\dumpbin.exe" -ErrorAction SilentlyContinue) | Select-Object -First 1
if (-not $dumpbin) { throw "dumpbin.exe not found under $vsRoot" }
$crt = Get-ChildItem "$vsRoot\VC\Redist\MSVC\*\$Arch\Microsoft.VC14*.CRT" -Directory | Sort-Object FullName | Select-Object -Last 1
if (-not $crt) { throw "the $Arch VC++ redistributable is not installed under $vsRoot" }
# vcomp140.dll (MSVC's OpenMP runtime, which ggml-base imports on x64) ships in its own folder.
$omp = Get-ChildItem "$vsRoot\VC\Redist\MSVC\*\$Arch\Microsoft.VC14*.OpenMP" -Directory -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
$redistDirs = @($crt.FullName) + @($omp | ForEach-Object { $_.FullName })
Write-Host "dumpbin: $($dumpbin.FullName)"
Write-Host "$Arch VC++ redist: $($redistDirs -join ', ')"

if ($BinDir) {
  if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
  New-Item -ItemType Directory -Force $OutDir | Out-Null
  foreach ($exe in 'audiocpp_server.exe', 'audiocpp_cli.exe') {
    $src = Join-Path $BinDir $exe
    if (-not (Test-Path $src)) { throw "$exe is missing from $BinDir" }
    Copy-Item $src $OutDir
  }
  Get-ChildItem $BinDir -Filter *.dll -File | Copy-Item -Destination $OutDir
  $data = Join-Path $BinDir 'espeak-ng-data.bin'
  if (Test-Path $data) {
    Copy-Item $data $OutDir
    New-Item -ItemType Directory -Force (Join-Path $OutDir 'licenses') | Out-Null
    Copy-Item (Join-Path $BinDir 'licenses\espeak-ng') (Join-Path $OutDir 'licenses') -Recurse -Force
  }
  Copy-Item LICENSE (Join-Path $OutDir 'LICENSE')
}
foreach ($required in 'audiocpp_server.exe', 'audiocpp_cli.exe', 'LICENSE') {
  if (-not (Test-Path (Join-Path $OutDir $required))) { throw "$required is missing from $OutDir" }
}
if ($Espeak) {
  foreach ($required in 'espeak-ng-data.bin', 'licenses\espeak-ng\COPYING') {
    if (-not (Test-Path (Join-Path $OutDir $required))) { throw "$required is missing from $OutDir (static eSpeak-ng)" }
  }
}
if ($CudaDll) { Copy-Item $CudaDll $OutDir -Force }

$cudaBins = @()
if ($CudaRoot) {
  foreach ($d in "bin\$Arch", 'bin') {
    $p = Join-Path $CudaRoot $d
    if (Test-Path $p) { $cudaBins += $p }
  }
}
$system32 = Join-Path $env:SystemRoot 'System32'
$allowed = @('nvcuda.dll') + $AllowMissing
$problems = @()

function Get-Dependents([string]$file) {
  $out = & $dumpbin.FullName /nologo /dependents $file
  if ($LASTEXITCODE -ne 0) { throw "dumpbin failed on $file" }
  $out | Where-Object { $_ -match '^\s+([A-Za-z0-9_.\-]+\.dll)\s*$' } | ForEach-Object { $Matches[1] }
}

for ($round = 0; $round -lt 5; $round++) {
  $added = $false
  foreach ($bin in Get-ChildItem $OutDir -File | Where-Object { $_.Extension -in '.exe', '.dll' }) {
    foreach ($dep in Get-Dependents $bin.FullName) {
      if (Test-Path (Join-Path $OutDir $dep)) { continue }
      if ($allowed | Where-Object { $dep -like $_ }) { continue }
      $fromCuda = $cudaBins | ForEach-Object { Join-Path $_ $dep } | Where-Object { Test-Path $_ } | Select-Object -First 1
      if ($fromCuda) { Copy-Item $fromCuda $OutDir; $added = $true; Write-Host "+ $dep (CUDA)"; continue }
      # Before the System32 check: a runner has VS's runtimes there, a user's machine may not.
      $fromRedist = $redistDirs | ForEach-Object { Join-Path $_ $dep } | Where-Object { Test-Path $_ } | Select-Object -First 1
      if ($fromRedist) { Copy-Item $fromRedist $OutDir; $added = $true; Write-Host "+ $dep (VC++ redist)"; continue }
      if ($dep -like 'api-ms-win-*' -or $dep -like 'ext-ms-win-*' -or (Test-Path (Join-Path $system32 $dep))) { continue }
      $problems += "$($bin.Name) imports $dep, which is neither in the archive nor an OS DLL"
    }
  }
  if (-not $added) { break }
}
$problems = $problems | Sort-Object -Unique
if ($problems) { $problems | Write-Host; throw "unresolved imports" }

& (Join-Path $PSScriptRoot 'check-pe.ps1') -Dir $OutDir -Machine $Arch

if ($CheckSm) {
  $cuobjdump = Join-Path $CudaRoot 'bin\cuobjdump.exe'
  if (-not (Test-Path $cuobjdump)) { $cuobjdump = 'cuobjdump.exe' }
  $elf = & $cuobjdump --list-elf (Join-Path $OutDir 'ggml-cuda.dll')
  $elf | Write-Host
  if (-not ($elf -match $CheckSm)) { throw "ggml-cuda.dll carries no $CheckSm code" }
}

Get-ChildItem $OutDir -Recurse -File | Format-Table FullName, Length -AutoSize | Out-String | Write-Host
