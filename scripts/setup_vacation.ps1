<#
Vacation mode setup — one script, ~2 minutes.

Configures GitHub Actions to run the daily hunt in the cloud and send new
rooms to your phone via Telegram (see VACATION.md for the full guide):
  1. Uploads your local config.yaml as a repo secret (never committed).
  2. Copies optional keys from .env (TfL, DeepSeek, proxy) into secrets.
  3. Generates the STATE_KEY that encrypts the cloud seen-database.
  4. Connects your Telegram bot (auto-detects your chat id from a test DM).
  5. Kicks off a test run so you can watch your phone light up.

Prereqs:
  - GitHub CLI authenticated:  gh auth login
  - A Telegram bot token: message @BotFather in Telegram -> /newbot (1 min).
  - The vacation-hunt workflow merged to your default branch.

Run from the repo root:
  powershell -ExecutionPolicy Bypass -File scripts\setup_vacation.ps1
#>

$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Ok($msg) { Write-Host "  OK  $msg" -ForegroundColor Green }

# --- sanity ------------------------------------------------------------------
# (cmd /c so gh's chatty stderr can't trip PowerShell 5.1's error handling)
cmd /c "gh auth status >nul 2>nul"
if ($LASTEXITCODE -ne 0) { Fail "GitHub CLI not authenticated - run: gh auth login" }
$repo = gh repo view --json nameWithOwner -q .nameWithOwner
if (-not $repo) { Fail "Run this from inside your Flatfinder repo clone." }
Write-Host "Setting up vacation mode for $repo" -ForegroundColor Cyan

$existingSecrets = (gh secret list --repo $repo | Out-String)

# --- 1. personal config -> secret ---------------------------------------------
# Same resolution as the app: FLATFINDER_HOME if set, else the repo folder.
$homeDir = $env:FLATFINDER_HOME
if (-not $homeDir) { $homeDir = (Get-Location).Path }
$configPath = Join-Path $homeDir "config.yaml"
if (-not (Test-Path $configPath)) {
    Fail "config.yaml not found at $configPath - run 'flatfinder setup' first."
}
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($configPath))
gh secret set FLATFINDER_CONFIG_B64 --repo $repo --body $b64
Ok "config.yaml -> secret FLATFINDER_CONFIG_B64 (from $configPath)"

# --- 2. optional keys from .env ------------------------------------------------
$envPath = Join-Path $homeDir ".env"
foreach ($name in @("TFL_APP_KEY", "TFL_APP_ID", "DEEPSEEK_API_KEY", "PROXY_URL")) {
    if (-not (Test-Path $envPath)) { break }
    $line = Select-String -Path $envPath -Pattern "^\s*$name\s*=\s*(.+)\s*$" | Select-Object -First 1
    if ($line) {
        $val = $line.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
        if ($val) {
            gh secret set $name --repo $repo --body $val
            Ok "$name -> secret (from .env)"
        }
    }
}

# --- 3. state encryption key ---------------------------------------------------
# Never regenerate an existing key: it would orphan the encrypted seen-DB
# already sitting on the runner-state branch.
if ($existingSecrets -match "(?m)^STATE_KEY\b") {
    Ok "STATE_KEY already set - keeping it (existing cloud state stays readable)"
} else {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    gh secret set STATE_KEY --repo $repo --body ([Convert]::ToBase64String($bytes))
    Ok "STATE_KEY generated -> secret (encrypts the seen-DB in the cloud)"
}

# --- 4. Telegram ----------------------------------------------------------------
$haveTelegram = ($existingSecrets -match "(?m)^TELEGRAM_BOT_TOKEN\b") -and
                ($existingSecrets -match "(?m)^TELEGRAM_CHAT_ID\b")
$reconfigure = $true
if ($haveTelegram) {
    $ans = Read-Host "Telegram already connected - reconfigure? (y/N)"
    if ($ans -ne "y") { $reconfigure = $false; Ok "keeping existing Telegram connection" }
}
if ($reconfigure) {
    Write-Host ""
    Write-Host "Telegram bot: in the Telegram app, message @BotFather -> /newbot," -ForegroundColor Cyan
    Write-Host "name it anything, and paste the token it gives you below." -ForegroundColor Cyan
    $token = Read-Host "Bot token"
    if (-not $token) { Fail "No token given." }

    Write-Host ""
    Write-Host "Now open a chat with YOUR new bot in Telegram and send it any message" -ForegroundColor Cyan
    Read-Host "(e.g. 'hi') - then press Enter here"
    $updates = Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates"
    $chatId = $updates.result | ForEach-Object { $_.message.chat.id } | Where-Object { $_ } | Select-Object -Last 1
    if (-not $chatId) {
        Fail "No message found. Send your bot a message (not to BotFather!) and re-run this script."
    }

    gh secret set TELEGRAM_BOT_TOKEN --repo $repo --body $token
    gh secret set TELEGRAM_CHAT_ID --repo $repo --body "$chatId"
    Ok "Telegram connected (chat id $chatId)"

    $null = Invoke-RestMethod -Method Post "https://api.telegram.org/bot$token/sendMessage" `
        -Body @{ chat_id = $chatId; text = "Flatfinder connected - vacation mode ready. New rooms will arrive here." }
    Ok "test message sent - check your phone"
}

# --- 5. test run -----------------------------------------------------------------
Write-Host ""
$run = Read-Host "Trigger a test run now? First run baselines (marks current rooms as seen), so expect a 'no new rooms' message (Y/n)"
if ($run -ne "n") {
    cmd /c "gh workflow run vacation-hunt.yml --repo $repo"
    if ($LASTEXITCODE -eq 0) {
        Ok "test run started - your phone should get a Telegram message in ~5 min"
    } else {
        Write-Host "  !! Could not start the workflow - is vacation-hunt.yml merged to your default branch?" -ForegroundColor Yellow
    }
}
Write-Host ""
Write-Host "Done. Watch runs at: https://github.com/$repo/actions/workflows/vacation-hunt.yml" -ForegroundColor Cyan
Write-Host "It now runs automatically every 2 hours, ~07:23-21:23 London time."
Write-Host "On-demand from your phone: GitHub app -> repo -> Actions -> Vacation hunt -> Run workflow."
