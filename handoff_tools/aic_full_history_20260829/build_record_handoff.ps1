[CmdletBinding()]
param(
    [string]$SourceRoot = "D:\02_Projects\ML\jinyinsai",
    [string]$OutputRoot = "D:\02_Projects\ML\jinyinsai\handoff\aic_full_history_20260829\build",
    [string]$Python = "D:\04_Tools\Python\python.exe"
)

$ErrorActionPreference = "Stop"
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$packageName = "aic_record_handoff_20260829_$stamp"
$package = Join-Path $OutputRoot $packageName
$snapshot = Join-Path $package "record_snapshot"
$memories = Join-Path $package "agent_memories"
$history = Join-Path $package "git_history"
$scratch = Join-Path $OutputRoot ("scratch_" + $stamp)

New-Item -ItemType Directory -Force -Path $snapshot,$memories,$history,$scratch | Out-Null

function Copy-PreservedFile {
    param([string]$From,[string]$ToRoot,[string]$Relative)
    $dest = Join-Path $ToRoot $Relative
    $parent = Split-Path -Parent $dest
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $From -Destination $dest -Force
}

$skipTop = @(
    ".git", "data", "handoff", "tmp", "__pycache__", ".pytest_cache",
    ".pytest_handoff_project_20260816_1828", ".playwright-mcp"
)
$heavyExt = @(
    ".pt", ".pth", ".ckpt", ".safetensors", ".bin", ".npy", ".npz",
    ".pkl", ".pickle", ".tgz", ".tar", ".gz", ".7z"
)
$runtimeExt = @(
    ".exe", ".dll", ".pyd", ".lib", ".obj", ".chm", ".whl"
)
$credentialNames = @(
    "direct_upload_secret.txt", "local_archive_token.txt", "xgpu_upload_ed25519",
    "xgpu_upload_ed25519.pub", "auth.json", ".credentials.json"
)

$included = 0
$excluded = New-Object System.Collections.Generic.List[object]
$files = Get-ChildItem -LiteralPath $source -Recurse -File -Force -ErrorAction SilentlyContinue
foreach ($file in $files) {
    $rel = [IO.Path]::GetRelativePath($source, $file.FullName)
    $parts = $rel -split '[\\/]'
    $top = $parts[0]
    $reason = $null

    if ($skipTop -contains $top -or $top -like ".pytest_handoff*") {
        $reason = "excluded directory"
    } elseif ($rel -like ".codex\bin\*" -or $rel -like ".codex\mcp_servers\*") {
        $reason = "third-party runtime cache"
    } elseif ($rel -like ".claude\*.backup.*" -or $rel -like ".claude\*.bak") {
        $reason = "stale agent configuration backup"
    } elseif ($credentialNames -contains $file.Name -or $file.Name -match '(?i)cookie|credential|private.?key') {
        $reason = "credential-bearing path"
    } elseif ($heavyExt -contains $file.Extension.ToLowerInvariant()) {
        $reason = "model/data/cache artifact"
    } elseif ($runtimeExt -contains $file.Extension.ToLowerInvariant()) {
        $reason = "compiled dependency"
    } elseif ($file.Extension -eq ".zip" -and $rel -notlike "submissions\*" -and $rel -notlike "runs\*") {
        $reason = "non-submission archive"
    }

    if ($reason) {
        $excluded.Add([pscustomobject]@{path=$rel;bytes=$file.Length;reason=$reason})
        continue
    }

    Copy-PreservedFile -From $file.FullName -ToRoot $snapshot -Relative $rel
    $included += 1
}

# Codex machine-generated memory (not the multi-gigabyte raw session store).
$codexMemory = "C:\Users\19811\.codex\memories"
if (Test-Path -LiteralPath $codexMemory) {
    Get-ChildItem -LiteralPath $codexMemory -Recurse -File -Force | Where-Object {
        $_.FullName -notmatch '\\.git\\'
    } | ForEach-Object {
        $rel = [IO.Path]::GetRelativePath($codexMemory, $_.FullName)
        Copy-PreservedFile -From $_.FullName -ToRoot (Join-Path $memories "codex") -Relative $rel
    }
}
Copy-PreservedFile -From "C:\Users\19811\.codex\AGENTS.md" -ToRoot $memories -Relative "codex_AGENTS.md"

# Claude Code memory plus this project's session history.
$claudeProject = "C:\Users\19811\.claude\projects\d--02-Projects-ML-jinyinsai"
if (Test-Path -LiteralPath $claudeProject) {
    Get-ChildItem -LiteralPath $claudeProject -Recurse -File -Force | ForEach-Object {
        $rel = [IO.Path]::GetRelativePath($claudeProject, $_.FullName)
        Copy-PreservedFile -From $_.FullName -ToRoot (Join-Path $memories "claude_project") -Relative $rel
    }
}
Copy-PreservedFile -From "C:\Users\19811\.claude\CLAUDE.md" -ToRoot $memories -Relative "claude_GLOBAL_CLAUDE.md"

# Redact live credentials in copied text while keeping the surrounding operational memory.
$textExt = @(".md", ".txt", ".log", ".json", ".jsonl", ".toml", ".yaml", ".yml", ".py", ".ps1", ".mjs", ".js", ".sh", ".csv")
Get-ChildItem -LiteralPath $package -Recurse -File | Where-Object {
    $textExt -contains $_.Extension.ToLowerInvariant() -and $_.Length -lt 300MB
} | ForEach-Object {
    $path = $_.FullName
    $text = [IO.File]::ReadAllText($path)
    $original = $text
    $text = [regex]::Replace($text, '(?i)(-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)[\s\S]*?(-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)', '<REDACTED_PRIVATE_KEY>')
    $text = [regex]::Replace($text, '(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._-]{16,}', '$1<REDACTED>')
    $text = [regex]::Replace($text, '(?i)([?&]token=)[^&\s"''`]+', '$1<REDACTED>')
    $text = [regex]::Replace($text, '(?i)((?:api[_-]?key|access[_-]?token|jupyter[_-]?token|password|凭据)[^\r\n]{0,80}?[=:：`"'']\s*)[A-Za-z0-9._-]{16,}', '$1<REDACTED>')
    $text = [regex]::Replace($text, '(?i)(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,})', '<REDACTED_TOKEN>')
    if ($text -ne $original) {
        [IO.File]::WriteAllText($path, $text, [Text.UTF8Encoding]::new($false))
    }
}

# Build a sanitized all-ref Git history in an isolated mirror.
$mirror = Join-Path $scratch "jinyinsai-sanitized.git"
git clone --mirror --no-local $source $mirror | Out-Host
if ($LASTEXITCODE -ne 0) { throw "git mirror clone failed" }
& $Python -m git_filter_repo --force --source $mirror --target $mirror `
    --path ".codex/direct_upload_secret.txt" `
    --path ".codex/local_archive_token.txt" `
    --path ".codex/xgpu_upload_ed25519" `
    --path ".codex/xgpu_upload_ed25519.pub" `
    --invert-paths | Out-Host
if ($LASTEXITCODE -ne 0) { throw "git history sanitization failed" }
$bundle = Join-Path $history "jinyinsai_sanitized_all_refs.bundle"
git -C $mirror bundle create $bundle --all | Out-Host
if ($LASTEXITCODE -ne 0) { throw "git bundle creation failed" }
git bundle verify $bundle | Out-Host
if ($LASTEXITCODE -ne 0) { throw "git bundle verification failed" }

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination (Join-Path $package "README.md") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "EXCLUSIONS.md") -Destination (Join-Path $package "EXCLUSIONS.md") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify_record_handoff.ps1") -Destination (Join-Path $package "verify_record_handoff.ps1") -Force

$excluded | Sort-Object path | Export-Csv -LiteralPath (Join-Path $package "EXCLUDED_PATHS.csv") -NoTypeInformation -Encoding utf8

$artifactRows = @(
    [pscustomobject]@{
        name="legal_b448_aligned_formal_v2_full.pt"; score="78.8561"; status="legal_reference";
        local_path="runs/aic_b448_aligned_formal_v2_20260713/collected/full.pt";
        bytes=783174886; sha256="8a349c46647166dcb4c0758f26cc8bde1926dfc7ed3b5b2a57c814b9d0d0c73a";
        remote_url="https://github.com/WRw5w/aic_new/releases/download/handoff-v1-20260816/full.pt"
    },
    [pscustomobject]@{
        name="pe_core_g14_lora_ep04.pt"; score="92.0014"; status="invalid_probe_only";
        local_path="remote_results/pe_core_g14_lora_20260702_185158/extracted/root/pe_lora_ckpt/ep04.pt";
        bytes=83929597; sha256="70c3d838dfa82bada7e734d5da5f1dd1cfbb496e57fa2747e729f98778571b73";
        remote_url="RELEASE_ASSET_PENDING"
    }
)
$artifactRows | Export-Csv -LiteralPath (Join-Path $package "ARTIFACT_POINTERS.csv") -NoTypeInformation -Encoding utf8

$manifestRows = Get-ChildItem -LiteralPath $package -Recurse -File | Where-Object {
    $_.Name -ne "MANIFEST.sha256.csv"
} | ForEach-Object {
    [pscustomobject]@{
        path=[IO.Path]::GetRelativePath($package, $_.FullName).Replace('\','/')
        bytes=$_.Length
        sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
    }
}
$manifestRows | Sort-Object path | Export-Csv -LiteralPath (Join-Path $package "MANIFEST.sha256.csv") -NoTypeInformation -Encoding utf8

$zip = Join-Path $OutputRoot ($packageName + ".zip")
Compress-Archive -LiteralPath $package -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()

Write-Output ("PACKAGE_ROOT=" + $package)
Write-Output ("PACKAGE_ZIP=" + $zip)
Write-Output ("PACKAGE_ZIP_BYTES=" + (Get-Item -LiteralPath $zip).Length)
Write-Output ("PACKAGE_ZIP_SHA256=" + $zipHash)
Write-Output ("RECORD_FILES_INCLUDED=" + $included)
Write-Output ("RECORD_FILES_EXCLUDED=" + $excluded.Count)
