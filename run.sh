#!/bin/bash
# claw - Claude-Like Agent Workflow 起動スクリプト
# 使い方: bash run.sh [--debug] [--work-dir <path>]

set -e

# スクリプトのあるディレクトリを基準にする（どこから実行しても動作する）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".app/venv"
REQUIREMENTS=".app/requirements.txt"
PYTHON_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"

# ============================================================
# venv がなければ作成してパッケージをインストール
# ============================================================
if [ ! -d "$VENV_DIR" ]; then
    echo "[SETUP] 仮想環境が見つかりません。$VENV_DIR を作成します..."

    # python3 コマンドを確認
    if ! command -v python3 &>/dev/null; then
        echo "[ERROR] python3 が見つかりません。Pythonをインストールしてください。"
        exit 1
    fi

    python3 -m venv "$VENV_DIR"
    echo "[SETUP] 仮想環境を作成しました: $VENV_DIR"

    echo "[SETUP] パッケージをインストールします..."
    "$PIP_BIN" install --upgrade pip -q
    "$PIP_BIN" install -r "$REQUIREMENTS"
    echo "[SETUP] インストール完了。"

else
    # venvはあるが requirements.txt が更新されている場合も対応
    # （requirements.txt が venv より新しければ再インストール）
    if [ "$REQUIREMENTS" -nt "$VENV_DIR/pyvenv.cfg" ]; then
        echo "[SETUP] requirements.txt が更新されています。パッケージを更新します..."
        "$PIP_BIN" install -r "$REQUIREMENTS" -q
        # タイムスタンプを更新して次回スキップ
        touch "$VENV_DIR/pyvenv.cfg"
        echo "[SETUP] 更新完了。"
    fi
fi

# ============================================================
# .env が存在しない場合は案内して終了
# ============================================================
if [ ! -f ".env" ]; then
    echo ""
    echo "================================================"
    echo "  .env ファイルが見つかりません。"
    echo "  以下のコマンドでテンプレートをコピーして設定してください:"
    echo ""
    echo "    cp .app/.env.example .env"
    echo "    # .env を編集して OPENROUTER_API_KEY を設定"
    echo "================================================"
    echo ""
    exit 1
fi

# ============================================================
# claw を起動（引数をそのまま渡す）
# ============================================================
exec "$PYTHON_BIN" .app/src/main.py "$@"
