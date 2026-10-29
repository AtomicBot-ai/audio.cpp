# Fail unless every .exe and .dll under -Dir is a PE image for -Machine (arm64 = 0xAA64, x64 = 0x8664).
# Reads the PE header directly, so it needs no Visual Studio and runs on any Windows runner.
param(
  [Parameter(Mandatory)][string]$Dir,
  [ValidateSet('arm64', 'x64')][string]$Machine = 'arm64'
)
$ErrorActionPreference = 'Stop'

$expected = @{ arm64 = 0xAA64; x64 = 0x8664 }[$Machine]
$bad = @()
$files = Get-ChildItem $Dir -File -Recurse | Where-Object { $_.Extension -in '.exe', '.dll' }
if (-not $files) { throw "no exe/dll under $Dir" }
foreach ($file in $files) {
  $stream = [System.IO.File]::OpenRead($file.FullName)
  try {
    $reader = New-Object System.IO.BinaryReader($stream)
    $stream.Seek(0x3C, 'Begin') | Out-Null
    $peOffset = $reader.ReadInt32()
    $stream.Seek($peOffset, 'Begin') | Out-Null
    if ($reader.ReadUInt32() -ne 0x00004550) { $bad += "$($file.Name): not a PE image"; continue }
    $machineId = $reader.ReadUInt16()
  } finally {
    $stream.Dispose()
  }
  $label = '0x{0:X4}' -f $machineId
  if ($machineId -ne $expected) { $bad += "$($file.Name): machine $label" } else { Write-Host "$($file.Name): $Machine" }
}
if ($bad) { $bad | Write-Host; throw "not $Machine`: $($bad.Count) file(s)" }
