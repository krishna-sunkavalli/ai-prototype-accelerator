#!/usr/bin/env bash
# accelerator/scripts/publish-base-image.sh — Publish the dependency base image.
#
# azd remote builds (az acr build) have no layer cache, so every deploy
# reinstalls the full Python stack (~3-7 minutes). Publishing this base once
# per requirements.txt revision cuts per-build image time to seconds:
#
#   1. Run a first full build (@devlead build) so the ACR exists.
#   2. bash accelerator/scripts/publish-base-image.sh
#   3. Put the printed ref into spec.yaml:  deployment.base_image: <ref>
#   4. Rebuild — the incremental planner reruns only hydration + deploy.
#
# The tag is derived from the sha256 of requirements.txt, so re-running after
# a dependency change publishes a new tag and the old one keeps working for
# older specs. Requires: az CLI logged in, and a completed provision (ACR
# name is read from generated/build-state/manifest.json).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="$ROOT/generated/build-state/manifest.json"
PROTO="$ROOT/generated/prototype"
REQUIREMENTS="$PROTO/backend/requirements.txt"

if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: $MANIFEST not found — run @devlead build (through provisioning) first." >&2
  exit 1
fi
if [[ ! -f "$REQUIREMENTS" ]]; then
  echo "ERROR: $REQUIREMENTS not found — run @devlead build first." >&2
  exit 1
fi

ACR_NAME="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['resources']['acrName'])")"
TAG="$(python3 -c "import hashlib;print(hashlib.sha256(open('$REQUIREMENTS','rb').read()).hexdigest()[:12])")"
IMAGE="prototype-base:$TAG"
REF="$ACR_NAME.azurecr.io/$IMAGE"

echo "Publishing dependency base image"
echo "  ACR   : $ACR_NAME"
echo "  Image : $IMAGE  (tag = sha256(requirements.txt)[:12])"

if az acr repository show --name "$ACR_NAME" --image "$IMAGE" >/dev/null 2>&1; then
  echo "  Already published — nothing to do."
else
  az acr build \
    --registry "$ACR_NAME" \
    --image "$IMAGE" \
    --file "$PROTO/Dockerfile.base" \
    "$PROTO"
fi

echo
echo "Done. Enable fast builds by adding this line under deployment: in spec.yaml:"
echo
echo "  base_image: $REF"
echo
echo "Then rerun @devlead build — only hydration and deploy will rerun."
