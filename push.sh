#!/bin/bash
# push.sh — 現在ブランチ + dist/openrouter + dist/azure を一括 push するスクリプト
#
# 使い方: bash push.sh
#
# 動作:
#   1. 現在ブランチをそのまま push
#   2. dist/openrouter ブランチに merge → .env.example を OpenRouter 版に差し替え → push
#   3. dist/azure ブランチに merge → .env.example を Azure 版に差し替え → push
#   4. 元のブランチに戻る

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/.app"

CURRENT=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD)
echo "現在のブランチ: $CURRENT"

# ── 1. 現在ブランチを push ───────────────────────────────────────
echo ""
echo "[1/3] $CURRENT を push..."
git -C "$SCRIPT_DIR" push origin "$CURRENT"
echo "✓ $CURRENT を push しました"

# ── dist ブランチ共通処理 ─────────────────────────────────────────
_deploy_dist() {
    local branch="$1"
    local env_example_src="$2"
    local step="$3"

    echo ""
    echo "[$step/3] $branch に deploy..."

    # ブランチが存在しなければ現在ブランチから新規作成
    if ! git -C "$SCRIPT_DIR" show-ref --quiet "refs/heads/$branch"; then
        git -C "$SCRIPT_DIR" branch "$branch" "$CURRENT"
        echo "  ブランチを新規作成: $branch"
    fi

    git -C "$SCRIPT_DIR" checkout "$branch"

    # 現在ブランチをマージ（競合があれば中断）
    git -C "$SCRIPT_DIR" merge "$CURRENT" --no-edit

    # .env.example をバージョン固有のものに差し替え
    cp "$env_example_src" "$APP_DIR/.env.example"
    git -C "$SCRIPT_DIR" add "$APP_DIR/.env.example"

    # 差分があればコミット
    if ! git -C "$SCRIPT_DIR" diff --cached --quiet; then
        git -C "$SCRIPT_DIR" commit -m "dist: .env.example を ${branch} 版に更新"
    fi

    git -C "$SCRIPT_DIR" push origin "$branch"
    echo "✓ $branch を push しました"
}

# ── 2. dist/openrouter ───────────────────────────────────────────
_deploy_dist "dist/openrouter" "$APP_DIR/.env.openrouter.example" "2"

# ── 3. dist/azure ────────────────────────────────────────────────
_deploy_dist "dist/azure" "$APP_DIR/.env.azure.example" "3"

# ── 4. 元のブランチに戻る ─────────────────────────────────────────
echo ""
git -C "$SCRIPT_DIR" checkout "$CURRENT"
echo "✓ $CURRENT に戻りました"
echo ""
echo "完了: 3 ブランチを push しました"
