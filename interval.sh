#!/bin/bash
set -euo pipefail

REPO_SLUG="harupipipipi/hurousyotokutest"
REPO_DIR="hurousyotokutest"

echo ""
echo "⏰ 通知間隔の変更"
echo "  1) 5 分    2) 15 分   3) 30 分"
echo "  4) 1 時間  5) 3 時間  6) カスタム"
read -rp "  > [1-6]: " SEL
case "$SEL" in
  1) CRON="*/5 * * * *" ;;
  2) CRON="*/15 * * * *" ;;
  3) CRON="*/30 * * * *" ;;
  4) CRON="0 * * * *" ;;
  5) CRON="0 */3 * * *" ;;
  6) read -rp "  cron: " CRON ;;
  *) echo "❌"; exit 1 ;;
esac

cd "$REPO_DIR" 2>/dev/null || { echo "❌ ${REPO_DIR} が見つかりません"; exit 1; }
git pull --rebase origin main 2>/dev/null || true

mkdir -p .github/workflows
cat > .github/workflows/lighter-bot.yml << WFEOF
name: Lighter Portfolio Bot

on:
  schedule:
    - cron: '${CRON}'
  workflow_dispatch: {}

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - uses: actions/cache@v4
        with:
          path: .cache
          key: lv4-\${{ github.run_id }}
          restore-keys: lv4-
      - run: pip install -r requirements.txt
      - run: python bot.py
        env:
          DISCORD_WEBHOOK_URL: \${{ secrets.DISCORD_WEBHOOK_URL }}
WFEOF

git add -A
git commit -m "interval: ${CRON}" 2>/dev/null || true
git push origin main

HAS_GH=false; command -v gh &>/dev/null && HAS_GH=true
if $HAS_GH; then
  gh workflow run lighter-bot.yml --repo="$REPO_SLUG" 2>/dev/null \
    && echo "✅ トリガー済み" || echo "⚠️  手動実行してください"
fi
echo "✅ 間隔: ${CRON}"
