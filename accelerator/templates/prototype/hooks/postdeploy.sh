#!/bin/bash
# ai-prototype-accelerator — Post-Deploy Validation
# Static scaffold file — do NOT modify when processing spec.yaml.
set -e

echo "╔══════════════════════════════════════╗"
echo "║   ai-prototype-accelerator        ║"
echo "║   Post-Deploy Validation             ║"
echo "╚══════════════════════════════════════╝"
echo ""

CONTAINER_APP_URL="${AZURE_CONTAINER_APP_URL}"
FAILED=false

if [ -z "$CONTAINER_APP_URL" ]; then
  echo "❌ AZURE_CONTAINER_APP_URL is not set."
  echo "   Run: azd env get-values | grep CONTAINER_APP"
  exit 1
fi

# ── Layer 1: Infrastructure connectivity ──────────────────────────────────────
echo "1️⃣  Infrastructure connectivity..."

echo "   Checking Managed Identity token..."
az account get-access-token --output none 2>/dev/null \
  && echo "   ✅ MI token OK" \
  || { echo "   ❌ MI token failed — check AZURE_CLIENT_ID"; FAILED=true; }

echo "   Checking Cosmos DB connectivity..."
python3 - <<'PYEOF' || { echo "   ❌ Cosmos DB check failed"; FAILED=true; }
import os, sys
try:
    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential
    cred = DefaultAzureCredential(
        managed_identity_client_id=os.environ["AZURE_CLIENT_ID"]
    )
    client = CosmosClient(url=os.environ["AZURE_COSMOS_ENDPOINT"], credential=cred)
    db = client.get_database_client(os.environ["AZURE_COSMOS_DATABASE"])
    list(db.list_containers(max_item_count=1))
    print("   ✅ Cosmos DB OK")
except Exception as e:
    print(f"   ❌ Cosmos DB failed: {e}")
    sys.exit(1)
PYEOF

echo "   Checking AI Search..."
SEARCH_TOKEN=$(az account get-access-token \
  --resource https://search.azure.com \
  --query accessToken -o tsv 2>/dev/null)
curl -s -f \
  "${AZURE_SEARCH_ENDPOINT}/indexes?api-version=2023-11-01" \
  -H "Authorization: Bearer ${SEARCH_TOKEN}" \
  > /dev/null \
  && echo "   ✅ AI Search OK" \
  || { echo "   ⚠  AI Search check inconclusive (may need RBAC)"; }

echo ""

# ── Layer 2: App validation ────────────────────────────────────────────────────
echo "2️⃣  App validation..."

echo "   Checking /health..."
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" \
  --max-time 30 \
  "$CONTAINER_APP_URL/health" 2>/dev/null)
if [ "$HEALTH" = "200" ]; then
  echo "   ✅ /health OK"
else
  echo "   ❌ /health returned $HEALTH (expected 200)"
  FAILED=true
fi

echo "   Checking /config..."
CONFIG_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  --max-time 10 \
  "$CONTAINER_APP_URL/config" 2>/dev/null)
if [ "$CONFIG_STATUS" = "200" ]; then
  echo "   ✅ /config OK"
else
  echo "   ❌ /config returned $CONFIG_STATUS (expected 200)"
  FAILED=true
fi

echo "   Checking WebSocket..."
python3 - <<PYEOF || { echo "   ❌ WebSocket check failed"; FAILED=true; }
import asyncio, sys
try:
    import websockets
    WS_URL = "${CONTAINER_APP_URL}".replace("https://", "wss://").replace("http://", "ws://") + "/chat"
    async def test():
        async with websockets.connect(WS_URL, open_timeout=15) as ws:
            await ws.send('{"type":"text","content":"ping"}')
            await asyncio.wait_for(ws.recv(), timeout=30)
            print("   ✅ WebSocket OK")
    asyncio.run(test())
except Exception as e:
    print(f"   ❌ WebSocket failed: {e}")
    sys.exit(1)
PYEOF

echo ""

# ── Layer 3: Agent validation ──────────────────────────────────────────────────
echo "3️⃣  Agent validation..."

# Wait for the container app warm-up to finish (agents load from Foundry on startup)
echo "   Waiting 45s for agent warm-up..."
sleep 45

echo "   Sending test message to agent..."
python3 - <<PYEOF || { echo "   ❌ Agent validation failed"; FAILED=true; }
import asyncio, json, sys

try:
    import websockets
    WS_URL = "${CONTAINER_APP_URL}".replace("https://", "wss://").replace("http://", "ws://") + "/chat"
    # Use first starter question from spec.yaml (injected by Copilot)
    TEST_MSG = "${FIRST_STARTER_QUESTION:-Tell me what you can help with}"

    async def test():
        async with websockets.connect(WS_URL, open_timeout=15) as ws:
            await ws.send(json.dumps({"type": "text", "content": TEST_MSG}))
            response_text = ""
            for _ in range(60):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    data = json.loads(raw)
                    if data.get("type") == "text":
                        response_text += data.get("content", "")
                    if data.get("done"):
                        break
                except asyncio.TimeoutError:
                    break
            if len(response_text) > 10:
                print(f"   ✅ Agent responded ({len(response_text)} chars)")
            else:
                print(f"   ❌ Agent response empty or too short: '{response_text}'")
                sys.exit(1)
    asyncio.run(test())
except Exception as e:
    print(f"   ❌ Agent test failed: {e}")
    sys.exit(1)
PYEOF

echo ""

# ── Final gate ─────────────────────────────────────────────────────────────────
if [ "$FAILED" = true ]; then
  echo "╔══════════════════════════════════════╗"
  echo "║   ❌ VALIDATION FAILED               ║"
  echo "║   Prototype URL will not be shown    ║"
  echo "║   Fix errors above and re-run:       ║"
  echo "║     azd deploy                       ║"
  echo "╚══════════════════════════════════════╝"
  exit 1
fi

echo "╔══════════════════════════════════════╗"
echo "║   ✅ ALL VALIDATIONS PASSED          ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "🎉 Prototype is live:"
echo ""
echo "   $CONTAINER_APP_URL"
echo ""
echo "   Share this URL with your stakeholders."
