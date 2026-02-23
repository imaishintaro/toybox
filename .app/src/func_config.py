"""設定・環境変数の読み込みモジュール"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def load_config() -> dict:
    """
    .envファイルから設定を読み込む。

    スクリプト位置を起点に上位ディレクトリを走査して .env を検索する。
    見つからない場合はカレントディレクトリから上位を探索する。

    Returns:
        設定辞書 (api_key, model, max_iterations, work_dir)

    Raises:
        SystemExit: 必須設定が不足している場合
    """
    env_path = _find_env_file()
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet").strip()
    max_iterations = int(os.getenv("MAX_ITERATIONS", "20"))
    work_dir = os.getenv("WORK_DIR", "").strip() or os.getcwd()

    if not api_key:
        print("[ERROR] OPENROUTER_API_KEY が設定されていません。")
        print("  .env.example を参考に .env ファイルを作成してください。")
        sys.exit(1)

    return {
        "api_key": api_key,
        "model": model,
        "max_iterations": max_iterations,
        "work_dir": str(Path(work_dir).resolve()),
        "env_path": str(env_path) if env_path else "(未検出)",
    }


def _find_env_file() -> Path | None:
    """
    .env ファイルを検索する。

    探索順序:
    1. このスクリプトファイルの親ディレクトリ群を上位に向けて走査
    2. カレントディレクトリから上位を走査

    Returns:
        .env ファイルのパス、見つからなければ None
    """
    # 1. スクリプト起点で上位を探索 (app/src/ → app/ → toy_code/ → ...)
    script_dir = Path(__file__).parent
    for parent in [script_dir, *script_dir.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate

    # 2. カレントディレクトリから上位を探索
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate

    return None
