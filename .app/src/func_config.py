"""設定・環境変数の読み込みモジュール"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def load_config() -> dict:
    """
    .env ファイルから設定を読み込む。

    対応プロバイダー:
      - openrouter (デフォルト): OPENROUTER_API_KEY / OPENROUTER_MODEL
      - azure: AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT /
               AZURE_OPENAI_DEPLOYMENT / AZURE_OPENAI_API_VERSION

    埋め込みモデル（コンテキスト圧縮用、省略可）:
      - EMBEDDING_MODEL: モデル名（省略時は圧縮しない）
      - EMBEDDING_PROVIDER: openrouter または azure（省略時はメインと同じ）
      - AZURE_EMBEDDING_DEPLOYMENT: Azure 使用時の埋め込みデプロイ名

    Returns:
        設定辞書

    Raises:
        SystemExit: 必須設定が不足している場合
    """
    env_path = _find_env_file()
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    provider = os.getenv("AI_PROVIDER", "openrouter").strip().lower()
    max_iterations = int(os.getenv("MAX_ITERATIONS", "20"))
    work_dir = os.getenv("WORK_DIR", "").strip() or os.getcwd()

    # ── チャットプロバイダー別の必須設定を読み込む ────────────────────
    if provider == "azure":
        api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
        azure_api_version = os.getenv(
            "AZURE_OPENAI_API_VERSION", "2024-08-01-preview"
        ).strip()
        model = azure_deployment  # 表示用

        missing = [
            name for name, val in [
                ("AZURE_OPENAI_API_KEY", api_key),
                ("AZURE_OPENAI_ENDPOINT", azure_endpoint),
                ("AZURE_OPENAI_DEPLOYMENT", azure_deployment),
            ] if not val
        ]
        if missing:
            print(f"[ERROR] Azure OpenAI の設定が不足しています: {', '.join(missing)}")
            sys.exit(1)

    else:
        # OpenRouter（デフォルト）
        provider = "openrouter"
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet").strip()
        azure_endpoint = azure_deployment = azure_api_version = ""

        if not api_key:
            print("[ERROR] OPENROUTER_API_KEY が設定されていません。")
            print("  .env ファイルを確認してください。")
            sys.exit(1)

    # OpenRouter キーは埋め込み用に常に保持しておく
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    # ── 埋め込みモデル設定（省略可） ─────────────────────────────────
    # EMBEDDING_PROVIDER が未指定ならメインプロバイダーと同じにする
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", provider).strip().lower()
    embedding_model = os.getenv("EMBEDDING_MODEL", "").strip()

    # Azure 埋め込みの場合は AZURE_EMBEDDING_DEPLOYMENT を使う
    if embedding_provider == "azure":
        azure_emb_deployment = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "").strip()
        if azure_emb_deployment:
            embedding_model = azure_emb_deployment
        # Azure 埋め込みに必要な資格情報は Azure チャットと共有
        embedding_azure_endpoint = azure_endpoint or os.getenv(
            "AZURE_OPENAI_ENDPOINT", ""
        ).strip()
        embedding_azure_api_version = azure_api_version or os.getenv(
            "AZURE_OPENAI_API_VERSION", "2024-08-01-preview"
        ).strip()
        embedding_api_key = api_key if provider == "azure" else os.getenv(
            "AZURE_OPENAI_API_KEY", ""
        ).strip()
    else:
        embedding_azure_endpoint = ""
        embedding_azure_api_version = ""
        embedding_api_key = openrouter_api_key or api_key

    return {
        # チャット設定
        "provider": provider,
        "api_key": api_key,
        "model": model,
        "max_iterations": max_iterations,
        "work_dir": str(Path(work_dir).resolve()),
        "env_path": str(env_path) if env_path else "(未検出)",
        # Azure チャット設定
        "azure_endpoint": azure_endpoint,
        "azure_deployment": azure_deployment,
        "azure_api_version": azure_api_version,
        # 埋め込み設定
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "embedding_api_key": embedding_api_key,
        "embedding_azure_endpoint": embedding_azure_endpoint,
        "embedding_azure_api_version": embedding_azure_api_version,
        # その他
        "openrouter_api_key": openrouter_api_key,
    }


def _find_env_file() -> Path | None:
    """
    .env ファイルを検索する。

    探索順序:
    1. このスクリプトファイルの親ディレクトリ群を上位に向けて走査
    2. カレントディレクトリから上位を走査
    """
    script_dir = Path(__file__).parent
    for parent in [script_dir, *script_dir.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate

    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate

    return None
