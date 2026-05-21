# ai-prototype-accelerator — Post-Deploy Validation (Windows/PowerShell)
# PowerShell equivalent of postdeploy.sh for Windows environments without WSL.
# Static scaffold file — do NOT modify when processing spec.yaml.
$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'

Write-Host "╔══════════════════════════════════════╗"
Write-Host "║   ai-prototype-accelerator        ║"
Write-Host "║   Post-Deploy Validation             ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""

$CONTAINER_APP_URL = $env:AZURE_CONTAINER_APP_URL
$FAILED = $false

if ([string]::IsNullOrEmpty($CONTAINER_APP_URL)) {
    Write-Host "❌ AZURE_CONTAINER_APP_URL is not set."
    Write-Host "   Run: azd env get-values | Select-String CONTAINER_APP"
    exit 1
}

# ── Layer 1: Infrastructure connectivity ──────────────────────────────────────
Write-Host "1️⃣  Infrastructure connectivity..."

Write-Host "   Checking Managed Identity token..."
az account get-access-token --output none 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   ✅ MI token OK"
} else {
    Write-Host "   ❌ MI token failed — check AZURE_CLIENT_ID"
    $FAILED = $true
}

Write-Host "   Checking AI Search..."
try {
    $SEARCH_TOKEN = az account get-access-token `
        --resource "https://search.azure.com" `
        --query accessToken -o tsv 2>$null
    $null = Invoke-RestMethod `
        -Uri "$($env:AZURE_SEARCH_ENDPOINT)/indexes?api-version=2023-11-01" `
        -Headers @{ Authorization = "Bearer $SEARCH_TOKEN" } `
        -TimeoutSec 15
    Write-Host "   ✅ AI Search OK"
} catch {
    Write-Host "   ⚠  AI Search check inconclusive (may need RBAC)"
}
Write-Host ""

# ── Layer 2: App validation ────────────────────────────────────────────────────
Write-Host "2️⃣  App validation..."

Write-Host "   Checking /health..."
try {
    $healthResponse = Invoke-WebRequest `
        -Uri "$CONTAINER_APP_URL/health" `
        -TimeoutSec 30 `
        -UseBasicParsing `
        -SkipHttpErrorCheck
    if ($healthResponse.StatusCode -eq 200) {
        Write-Host "   ✅ /health OK"
    } else {
        Write-Host "   ❌ /health returned $($healthResponse.StatusCode) (expected 200)"
        $FAILED = $true
    }
} catch {
    Write-Host "   ❌ /health request failed: $_"
    $FAILED = $true
}

Write-Host "   Checking /config..."
try {
    $configResponse = Invoke-WebRequest `
        -Uri "$CONTAINER_APP_URL/config" `
        -TimeoutSec 10 `
        -UseBasicParsing `
        -SkipHttpErrorCheck
    if ($configResponse.StatusCode -eq 200) {
        Write-Host "   ✅ /config OK"
    } else {
        Write-Host "   ❌ /config returned $($configResponse.StatusCode) (expected 200)"
        $FAILED = $true
    }
} catch {
    Write-Host "   ❌ /config request failed: $_"
    $FAILED = $true
}

Write-Host "   Checking WebSocket..."
$wsCheck = python -c @"
import asyncio, sys
try:
    import websockets
    WS_URL = '${CONTAINER_APP_URL}'.replace('https://', 'wss://').replace('http://', 'ws://') + '/chat'
    async def test():
        last_err = None
        for attempt in range(4):
            try:
                async with websockets.connect(WS_URL, open_timeout=20) as ws:
                    await ws.send('{\"type\":\"text\",\"content\":\"ping\"}')
                    await asyncio.wait_for(ws.recv(), timeout=30)
                    print('   ✅ WebSocket OK')
                    return
            except Exception as e:
                last_err = e
                await asyncio.sleep(8)
        raise last_err
    asyncio.run(test())
except Exception as e:
    print(f'   ❌ WebSocket failed: {e}')
    sys.exit(1)
"@ 2>&1
$wsCheck | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { $FAILED = $true }
Write-Host ""

# ── Layer 3: Agent validation ──────────────────────────────────────────────────
Write-Host "3️⃣  Agent validation..."

# Wait for the container app warm-up to finish (agents load from Foundry on startup)
Write-Host "   Waiting 45s for agent warm-up..."
Start-Sleep 45

$FIRST_STARTER_QUESTION = if ($env:FIRST_STARTER_QUESTION) { $env:FIRST_STARTER_QUESTION } else { "Tell me what you can help with" }

Write-Host "   Sending test message to agent..."
$agentCheck = python -c @"
import asyncio, json, sys
try:
    import websockets
    WS_URL = '${CONTAINER_APP_URL}'.replace('https://', 'wss://').replace('http://', 'ws://') + '/chat'
    TEST_MSG = '${FIRST_STARTER_QUESTION}'
    async def test():
        last_err = None
        for attempt in range(3):
            try:
                async with websockets.connect(WS_URL, open_timeout=20) as ws:
                    await ws.send(json.dumps({'type': 'text', 'content': TEST_MSG}))
                    response_text = ''
                    for _ in range(30):
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                            data = json.loads(raw)
                            if data.get('type') == 'text':
                                response_text += data.get('content', '')
                            if data.get('done'):
                                break
                        except asyncio.TimeoutError:
                            break
                    if len(response_text) > 10:
                        print(f'   ✅ Agent responded ({len(response_text)} chars)')
                        return
                    last_err = f'response too short: {repr(response_text)}'
            except Exception as e:
                last_err = str(e)
            await asyncio.sleep(10)
        print(f'   ❌ Agent test failed after retries: {last_err}')
        sys.exit(1)
    asyncio.run(test())
except Exception as e:
    print(f'   ❌ Agent test failed: {e}')
    sys.exit(1)
"@ 2>&1
$agentCheck | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { $FAILED = $true }
Write-Host ""

# ── Final gate ─────────────────────────────────────────────────────────────────
if ($FAILED) {
    Write-Host "╔══════════════════════════════════════╗"
    Write-Host "║   ❌ VALIDATION FAILED               ║"
    Write-Host "║   Prototype URL will not be shown    ║"
    Write-Host "║   Fix errors above and re-run:       ║"
    Write-Host "║     azd deploy                       ║"
    Write-Host "╚══════════════════════════════════════╝"
    exit 1
}

Write-Host "╔══════════════════════════════════════╗"
Write-Host "║   ✅ ALL VALIDATIONS PASSED          ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""
Write-Host "🎉 Prototype is live:"
Write-Host ""
Write-Host "   $CONTAINER_APP_URL"
Write-Host ""
Write-Host "   Share this URL with your stakeholders."
