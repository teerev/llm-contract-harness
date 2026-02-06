#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/user/repos/worldsim"
WO_DIR="/Users/user/repos/aos/wo4"
OUT_DIR="/Users/user/repos/aos/artifacts4"
MODEL="gpt-5.2"
MAX_ATTEMPTS=2

# Init the product repo (wipe and recreate to guarantee clean state)
if [ -d "$REPO" ]; then
  rm -rf "$REPO"
fi
mkdir -p "$REPO"
cd "$REPO"
git init
echo '__pycache__/' > .gitignore
echo '.pytest_cache/' >> .gitignore
git add -A
git commit --allow-empty -m "init"
cd -

for i in $(seq -w 1 12); do
  WO="$WO_DIR/WO-${i}.json"
  echo ""
  echo "========================================"
  echo "  Running WO-${i}"
  echo "========================================"
  echo ""

  python -m factory run \
    --repo "$REPO" \
    --work-order "$WO" \
    --out "$OUT_DIR" \
    --llm-model "$MODEL" \
    --max-attempts "$MAX_ATTEMPTS"

  echo ""
  echo "WO-${i} PASSED — committing..."
  cd "$REPO"
  git add -A
  git commit -m "WO-${i}"
  cd -
done

echo ""
echo "========================================"
echo "  All 12 work orders completed!"
echo "========================================"
