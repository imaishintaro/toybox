"""
セッション保存・復元モジュール。

会話履歴をJSONファイルに保存し、中断後も再開できるようにする。
セッションファイルは .app/sessions/ に保存される。
"""
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# セッション保存ディレクトリ (.app/sessions/)
SESSIONS_DIR = Path(__file__).parent.parent / "sessions"
AUTOSAVE_NAME = "autosave"


def save_session(
    conversation: list[dict],
    model: str,
    work_dir: str,
    name: str = AUTOSAVE_NAME,
) -> Path:
    """
    会話履歴をJSONファイルに保存する。

    Args:
        conversation: 会話履歴リスト
        model: 使用モデル名
        work_dir: 作業ディレクトリ
        name: セッション名 (ファイル名になる、拡張子不要)

    Returns:
        保存したファイルのパス
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # セッションデータ
    data = {
        "version": 1,
        "saved_at": datetime.now().isoformat(),
        "model": model,
        "work_dir": work_dir,
        "message_count": len(conversation),
        "conversation": conversation,
    }

    path = SESSIONS_DIR / f"{name}.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("セッション保存: %s (%d件)", path, len(conversation))
    return path


def load_session(name: str = AUTOSAVE_NAME) -> dict | None:
    """
    セッションファイルを読み込む。

    Args:
        name: セッション名

    Returns:
        セッションデータ辞書、見つからない場合は None
    """
    path = SESSIONS_DIR / f"{name}.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.info("セッション読み込み: %s (%d件)", path, data.get("message_count", 0))
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("セッション読み込み失敗: %s: %s", path, e)
        return None


def delete_session(name: str) -> bool:
    """
    セッションファイルを削除する。

    Args:
        name: セッション名

    Returns:
        削除成功なら True
    """
    path = SESSIONS_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def list_sessions() -> list[dict]:
    """
    利用可能なセッション一覧を返す（更新日時の降順）。

    Returns:
        セッション情報の辞書リスト
    """
    if not SESSIONS_DIR.exists():
        return []

    sessions: list[dict] = []
    for path in sorted(
        SESSIONS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            saved_at = data.get("saved_at", "")
            # 表示用に日時をフォーマット
            try:
                dt = datetime.fromisoformat(saved_at)
                saved_at_display = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                saved_at_display = saved_at

            sessions.append({
                "name": path.stem,
                "path": str(path),
                "model": data.get("model", "unknown"),
                "message_count": data.get("message_count", 0),
                "saved_at": saved_at_display,
                "work_dir": data.get("work_dir", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue

    return sessions


def autosave_exists() -> bool:
    """自動保存ファイルが存在するか確認する。"""
    return (SESSIONS_DIR / f"{AUTOSAVE_NAME}.json").exists()
