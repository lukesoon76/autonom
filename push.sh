#!/usr/bin/env bash
# One-shot: sign in to GitHub (if needed) and push Autonom.
# Usage:  ./push.sh          -> creates a PRIVATE repo named Autonom
#         ./push.sh public   -> creates it PUBLIC instead
set -euo pipefail
cd "$(dirname "$0")"

VIS="--private"
[ "${1:-}" = "public" ] && VIS="--public"

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) not found. Install it, then re-run ./push.sh"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "→ Not signed in. Launching GitHub login (follow the browser/device prompt)…"
  gh auth login
fi

if git remote get-url origin >/dev/null 2>&1; then
  echo "→ Remote 'origin' already set — pushing…"
  git push -u origin main
else
  echo "→ Creating repo Autonom ($VIS) and pushing…"
  gh repo create Autonom "$VIS" --source=. --remote=origin --push
fi

echo "✅ Done. Repo: $(gh repo view --json url -q .url 2>/dev/null || echo '(see GitHub)')"
