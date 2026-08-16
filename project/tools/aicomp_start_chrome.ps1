param(
  [switch]$Restart
)

$ErrorActionPreference = "Stop"

$profile = "D:\tmp\aicomp_chrome_profile"
$url = "https://reg.aicomp.cn/app/JSGLPT/639980063d903c241eb85102"
$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $chrome) {
  throw "Chrome executable not found."
}

if ($Restart) {
  $escapedProfile = [WildcardPattern]::Escape($profile)
  $targets = Get-CimInstance Win32_Process -Filter "name='chrome.exe'" |
    Where-Object { $_.CommandLine -like "*$escapedProfile*" -or $_.CommandLine -like "*aicomp_chrome_profile*" }
  foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force
  }
  Start-Sleep -Seconds 2
}

$args = @(
  "--remote-debugging-port=9222",
  "--user-data-dir=$profile",
  "--no-first-run",
  "--new-window",
  "--disable-background-timer-throttling",
  "--disable-backgrounding-occluded-windows",
  "--disable-renderer-backgrounding",
  "--disable-features=CalculateNativeWinOcclusion,MemorySaver",
  $url
)

Start-Process -FilePath $chrome -ArgumentList $args -WindowStyle Normal
Write-Host "AICOMP Chrome started with anti-throttling flags."
