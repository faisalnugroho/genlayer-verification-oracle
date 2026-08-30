#!/bin/bash
# gvo-tunnel-url-push: extract the current trycloudflare URL from the
# gvo-tunnel journal and push it to GitHub so the Vercel edge proxy can
# always find the backend (quick-tunnel URLs rotate on service restart).
set -u
JOURNAL_URL=$(journalctl -u gvo-tunnel --no-pager -n 200 2>/dev/null \
  | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
if [ -z "$JOURNAL_URL" ]; then
  echo "no tunnel URL found in journal" >&2
  exit 1
fi
REPO_DIR="/home/ubuntu/genlayer-verification-oracle"
FILE="$REPO_DIR/backend/TUNNEL_URL"
if [ -f "$FILE" ] && [ "$(cat "$FILE")" = "$JOURNAL_URL" ]; then
  echo "unchanged: $JOURNAL_URL"
  exit 0
fi
echo "$JOURNAL_URL" > "$FILE"
cd "$REPO_DIR" || exit 1
if git diff --quiet -- "$FILE" 2>/dev/null; then
  echo "no git change for $FILE"
  exit 0
fi
git add "$FILE"
git commit -m "chore(backend): update live tunnel URL" --quiet
if git push origin HEAD:main --quiet 2>&1; then
  echo "pushed: $JOURNAL_URL"
else
  echo "push failed" >&2
  exit 1
fi
