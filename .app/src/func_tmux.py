"""
tmux セッション・ウィンドウ管理モジュール。

claw のマルチエージェント実行時に:
  - orchestrator window: ユーザー ↔ オーケストレーター
  - shared     window: 全エージェント共有ボード
  - claw_1     window: エージェント1 のログ
  - claw_2     window: エージェント2 のログ
  ...
を作成・管理する。
"""
import os
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """tmux がインストールされているか確認する。"""
    return shutil.which("tmux") is not None


def in_tmux_session() -> bool:
    """現在 tmux セッション内で実行されているか確認する。"""
    return "TMUX" in os.environ


def current_session() -> str | None:
    """現在の tmux セッション名を返す。tmux 外なら None。"""
    if not in_tmux_session():
        return None
    result = _run(["tmux", "display-message", "-p", "#S"])
    return result.stdout.strip() if result.returncode == 0 else None


def session_exists(session_name: str) -> bool:
    """指定セッションが存在するか確認する。"""
    result = _run(["tmux", "has-session", "-t", session_name])
    return result.returncode == 0


def new_session(session_name: str, first_window: str = "orchestrator") -> None:
    """新しい tmux セッションをデタッチ状態で作成する。"""
    _run([
        "tmux", "new-session", "-d",
        "-s", session_name,
        "-n", first_window,
        "-x", "220", "-y", "50",
    ], check=True)
    logger.info("tmuxセッション作成: %s", session_name)


def kill_session(session_name: str) -> None:
    """tmux セッションを削除する。"""
    if session_exists(session_name):
        _run(["tmux", "kill-session", "-t", session_name])
        logger.info("tmuxセッション削除: %s", session_name)


def new_window(
    session_name: str,
    window_name: str,
    command: str = "",
    work_dir: str = "",
) -> None:
    """
    セッションに新しいウィンドウを追加する。

    Args:
        session_name: tmux セッション名
        window_name: ウィンドウ名
        command: 起動後に実行するコマンド
        work_dir: ウィンドウの作業ディレクトリ
    """
    args = ["tmux", "new-window", "-t", session_name, "-n", window_name]
    if work_dir:
        args += ["-c", work_dir]
    _run(args, check=True)

    if command:
        send_keys(session_name, window_name, command)


def send_keys(session_name: str, window_name: str, keys: str) -> None:
    """tmux ウィンドウにキーストロークを送信する。"""
    _run([
        "tmux", "send-keys",
        "-t", f"{session_name}:{window_name}",
        keys, "Enter",
    ])


def select_window(session_name: str, window_name: str) -> None:
    """指定ウィンドウをアクティブにする。"""
    _run(["tmux", "select-window", "-t", f"{session_name}:{window_name}"])


def attach(session_name: str) -> None:
    """tmux セッションにアタッチする（フォアグラウンドで実行される）。"""
    subprocess.run(["tmux", "attach-session", "-t", session_name])


def setup_project_session(
    session_name: str,
    agent_names: list[str],
    log_dir: str,
    board_path: str,
    work_dir: str,
    python_bin: str = "python3",
) -> str:
    """
    プロジェクト用の tmux セッションをセットアップする。

    orchestrator / shared / claw_1 / claw_2 ... のウィンドウを作成し、
    各エージェントウィンドウでログウォッチャーを起動する。

    Args:
        session_name: tmux セッション名
        agent_names: エージェント名のリスト (["claw_1", "claw_2", ...])
        log_dir: ログファイルのディレクトリ
        board_path: 共有ボードファイルのパス
        work_dir: 作業ディレクトリ
        python_bin: Python 実行ファイルのパス

    Returns:
        セッション名
    """
    # tmux 外ならば新規セッションを作成、tmux 内ならば現在セッションに追加
    if in_tmux_session():
        session = current_session() or session_name
        # orchestrator ウィンドウは既存ウィンドウ（カレント）なので作成しない
        _ensure_window(session, "shared", work_dir)
        for name in agent_names:
            _ensure_window(session, name, work_dir)
    else:
        session = session_name
        if not session_exists(session):
            new_session(session, "orchestrator")
        _ensure_window(session, "shared", work_dir)
        for name in agent_names:
            _ensure_window(session, name, work_dir)

    # 共有ボードウォッチャーを起動
    watch_script = _find_watch_script(python_bin)
    if watch_script:
        send_keys(
            session, "shared",
            f"{python_bin} {watch_script} {board_path} '📋 Shared Board'",
        )

    # 各エージェントのログウォッチャーを起動
    for name in agent_names:
        log_file = f"{log_dir}/{name}.log"
        if watch_script:
            send_keys(
                session, name,
                f"{python_bin} {watch_script} {log_file} '{name}'",
            )

    logger.info("tmuxセッションセットアップ完了: %s (windows: %s)", session, agent_names)
    return session


def _ensure_window(session: str, window_name: str, work_dir: str = "") -> None:
    """ウィンドウが存在しない場合のみ作成する。"""
    result = _run(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"])
    existing = result.stdout.strip().splitlines() if result.returncode == 0 else []
    if window_name not in existing:
        new_window(session, window_name, work_dir=work_dir)


def _find_watch_script(python_bin: str) -> str | None:
    """watch.py スクリプトのパスを探して返す。"""
    import sys
    from pathlib import Path
    # func_tmux.py と同じディレクトリの watch.py を探す
    script = Path(__file__).parent / "watch.py"
    return str(script) if script.exists() else None


def _run(args: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """tmux コマンドを実行する。"""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=check,
    )
