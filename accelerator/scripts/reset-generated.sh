#!/bin/bash
# ai-prototype-accelerator — Generated Prototype Reset
# Accelerator-maintained utility script.

echo "╔══════════════════════════════════════╗"
echo "║   ai-prototype-accelerator        ║"
echo "║   Generated Prototype Reset          ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "This will remove all emitted prototype files from"
echo "the previous accelerator build."
echo ""
echo "The following will be PRESERVED:"
echo "  ✅ accelerator/"
echo "  ✅ .github/"
echo "  ✅ docs and root metadata"
echo "  ✅ README.md"
echo "  ✅ spec.yaml (your use case spec)"
echo ""
echo "The following will be REMOVED:"
echo "  🗑  generated/prototype/*"
echo "  🗑  generated/build-state/*"
echo ""

if [ "$1" = "--yes" ]; then
  confirm="yes"
else
  read -p "Are you sure? Type 'yes' to confirm: " confirm
fi

if [ "$confirm" != "yes" ]; then
  echo ""
  echo "Cancelled. No files removed."
  exit 0
fi

echo ""
echo "Removing generated files..."

rm -rf generated/prototype/*
rm -rf generated/build-state/*

mkdir -p generated/prototype/agents/specialists
mkdir -p generated/prototype/agents/knowledge
mkdir -p generated/build-state
touch generated/prototype/agents/specialists/.gitkeep
touch generated/prototype/agents/knowledge/.gitkeep
touch generated/build-state/.gitkeep

echo "   ✅ Generated files removed"
echo ""
echo "✅ Reset complete."
echo ""
echo "Next steps:"
echo "  1. Drop your new spec.yaml in the repo root"
echo "  2. Open GitHub Copilot Chat"
echo "  3. Type: @devlead build"
echo "  4. Run deployment from generated/prototype"
