[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PackageRoot
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $PackageRoot).Path
$manifest = Join-Path $root "MANIFEST.sha256.csv"
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw "Manifest missing: $manifest"
}

$failures = @()
$rows = Import-Csv -LiteralPath $manifest
foreach ($row in $rows) {
    $path = Join-Path $root $row.path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $failures += "missing: $($row.path)"
        continue
    }
    $item = Get-Item -LiteralPath $path
    if ($item.Length -ne [int64]$row.bytes) {
        $failures += "size mismatch: $($row.path)"
        continue
    }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    if ($hash -ne $row.sha256.ToLowerInvariant()) {
        $failures += "sha256 mismatch: $($row.path)"
    }
}

$bundle = Join-Path $root "git_history\jinyinsai_sanitized_all_refs.bundle"
if (Test-Path -LiteralPath $bundle) {
    git bundle verify $bundle | Out-Host
    if ($LASTEXITCODE -ne 0) {
        $failures += "git bundle verify failed"
    }
} else {
    $failures += "git history bundle missing"
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Output "RECORD_HANDOFF_VERIFIED files=$($rows.Count)"
