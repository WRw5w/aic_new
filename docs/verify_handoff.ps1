[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PackageRoot,
    [string]$Checkpoint = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $PackageRoot).Path
$manifestPath = Join-Path $root "MANIFEST.sha256.csv"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Manifest missing: $manifestPath"
}

$rows = Import-Csv -LiteralPath $manifestPath
$failures = @()
foreach ($row in $rows) {
    $path = Join-Path $root $row.path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $failures += "missing: $($row.path)"
        continue
    }
    $file = Get-Item -LiteralPath $path
    if ($file.Length -ne [int64]$row.bytes) {
        $failures += "size mismatch: $($row.path)"
        continue
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    if ($actual -ne $row.sha256.ToLowerInvariant()) {
        $failures += "sha256 mismatch: $($row.path)"
    }
}

$forbidden = Get-ChildItem -LiteralPath $root -Recurse -Force | Where-Object {
    $_.Name -match '^\.env($|\.)|cookie|user.?data|chrome.?profile|credential|session' -or
    ($_.PSIsContainer -and $_.Name -in @("data", "train", "test", ".git", ".claude", ".codex"))
}
if ($forbidden) {
    $failures += "forbidden paths: $($forbidden.FullName -join ', ')"
}

if (-not [string]::IsNullOrWhiteSpace($Checkpoint)) {
    $checkpointFull = (Resolve-Path -LiteralPath $Checkpoint).Path
    $expected = "8a349c46647166dcb4c0758f26cc8bde1926dfc7ed3b5b2a57c814b9d0d0c73a"
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $checkpointFull).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        $failures += "checkpoint sha256 mismatch"
    }
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Output "HANDOFF_VERIFIED files=$($rows.Count)"
if (-not [string]::IsNullOrWhiteSpace($Checkpoint)) {
    Write-Output "CHECKPOINT_VERIFIED"
}

