#!/bin/bash
# claw 起動スクリプト
#
# .env に以下を設定してください:
#   CLAW_APP_PATH=/path/to/claw/.app   # ツール本体のパス（必須）
#   OPENROUTER_API_KEY=sk-or-v1-...    # API キー（必須）
#   OPENROUTER_MODEL=anthropic/claude-3.5-sonnet  # モデル（省略可）
#
# このスクリプトは .env と同じフォルダに置いて使う。
# プロジェクトフォルダ固有のコンテキスト/セッションは .claw/ に保存される。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── .env から CLAW_APP_PATH を取得 ─────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "エラー: .env が見つかりません: $ENV_FILE" >&2
    echo ".env を作成して CLAW_APP_PATH と API キーを設定してください。" >&2
    exit 1
fi

CLAW_APP_PATH=$(grep -E '^CLAW_APP_PATH=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs || true)

if [ -z "$CLAW_APP_PATH" ]; then
    echo "エラー: .env に CLAW_APP_PATH が設定されていません" >&2
    echo "例: CLAW_APP_PATH=/path/to/claw/.app" >&2
    exit 1
fi

# ~ を展開
CLAW_APP_PATH=$(eval echo "$CLAW_APP_PATH")

if [ ! -f "$CLAW_APP_PATH/src/main.py" ]; then
    echo "エラー: ツール本体が見つかりません: $CLAW_APP_PATH/src/main.py" >&2
    exit 1
fi

# ── venv のセットアップ（CLAW_APP_PATH/venv/ に共有） ──────────
VENV_DIR="$CLAW_APP_PATH/venv"
PYTHON_BIN="$VENV_DIR/bin/python"
REQUIREMENTS="$CLAW_APP_PATH/requirements.txt"

if [ ! -d "$VENV_DIR" ]; then
    echo "[SETUP] 仮想環境を作成します: $VENV_DIR"
    if ! command -v python3 &>/dev/null; then
        echo "[ERROR] python3 が見つかりません。" >&2
        exit 1
    fi
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r "$REQUIREMENTS"
    echo "[SETUP] セットアップ完了。"
elif [ "$REQUIREMENTS" -nt "$VENV_DIR/pyvenv.cfg" ]; then
    echo "[SETUP] requirements.txt が更新されています。パッケージを更新します..."
    "$VENV_DIR/bin/pip" install -r "$REQUIREMENTS" -q
    touch "$VENV_DIR/pyvenv.cfg"
fi

# ── claw を起動（--work-dir でプロジェクトフォルダを指定） ─────
exec "$PYTHON_BIN" "$CLAW_APP_PATH/src/main.py" --work-dir "$SCRIPT_DIR" "$@"
