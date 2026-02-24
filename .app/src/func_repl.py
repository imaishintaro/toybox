"""claw - REPL ループ + スラッシュコマンド + プロジェクト実行

インタラクティブ入力、セッション管理、マルチエージェントプロジェクト実行など
REPL に関するロジックをまとめたモジュール。
"""
import sys
import os
import time
import logging
import threading
from pathlib import Path

from rich.rule import Rule
from rich.text import Text
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import box

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.patch_stdout import patch_stdout as pt_patch_stdout
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from func_openrouter import OpenRouterClient, create_chat_client
from func_embedding import create_embedding_client
from func_agent import Agent
from func_session import (
    init as session_init,
    save_session, load_session, list_sessions,
    delete_session, autosave_exists, AUTOSAVE_NAME,
)
from func_orchestrator import plan_project, format_plan_display, ProjectPlan
from func_multi_agent import MultiAgentRunner
import func_tmux as tmux

from func_ui import (
    console,
    run_agent_turn,
    _print_welcome,
    _print_sessions,
    _print_stats,
    _print_help,
    _print_project_results,
    _show_board_hint,
)

logger = logging.getLogger(__name__)

# ── 入力欄（画面下部固定・2行対応） ────────────────────────────────
# Enter = 送信 / Alt+Enter = 改行
_input_kb = KeyBindings()


@_input_kb.add("enter")
def _kb_submit(event) -> None:
    event.current_buffer.validate_and_handle()


@_input_kb.add("escape", "enter")   # Alt+Enter
def _kb_newline(event) -> None:
    event.current_buffer.newline()


_input_session: PromptSession = PromptSession(history=InMemoryHistory())
_INPUT_PROMPT = ANSI("\033[1;36mYou\033[0m \033[2m›\033[0m ")

# ツールバー・空白行の背景を端末デフォルト色に合わせる
_PROMPT_STYLE = Style.from_dict({"bottom-toolbar": "bg:default noreverse"})


# ============================================================
# セッションヘルパー
# ============================================================

def _prompt_session_name() -> str:
    """セッション名を対話的に入力させる。"""
    return Prompt.ask("[cyan]セッション名 (空でキャンセル)[/cyan]", console=console, default="").strip()


def _load_session_into_agent(agent: Agent, name: str) -> None:
    """指定セッションをエージェントに読み込む。"""
    data = load_session(name)
    if not data:
        console.print(f"[bold red]セッションが見つかりません: {name}[/bold red]")
        return
    agent.reset_conversation()
    agent.load_conversation(data["conversation"])
    console.print(
        f"[dim cyan]✓ '{name}' を読み込みました ({data['message_count']} メッセージ)[/dim cyan]"
    )


def _offer_resume(agent: Agent) -> None:
    """自動保存セッションがあれば再開を提案する。"""
    data = load_session(AUTOSAVE_NAME)
    if not data or not data.get("conversation"):
        return

    saved_at = data.get("saved_at", "")
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(saved_at)
        saved_display = dt.strftime("%m/%d %H:%M")
    except (ValueError, TypeError):
        saved_display = saved_at

    console.print(
        f"[dim cyan]前回のセッションが見つかりました "
        f"({saved_display}、{data.get('message_count', 0)} メッセージ)[/dim cyan]"
    )
    if Confirm.ask("  前回の会話を引き継ぎますか？", console=console, default=True):
        _load_session_into_agent(agent, AUTOSAVE_NAME)
    console.print()


# ============================================================
# スラッシュコマンド処理
# ============================================================

def handle_slash_command(cmd: str, agent: Agent, config: dict) -> bool:
    """
    スラッシュコマンドを処理する。

    Returns:
        True なら REPL を終了する
    """
    parts = cmd.strip().split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    match command:
        case "/exit" | "/quit" | "/q":
            console.print("[dim]さようなら！[/dim]")
            return True

        case "/reset" | "/clear":
            agent.reset_conversation()
            console.print("[dim cyan]✓ 会話履歴をリセットしました。[/dim cyan]")

        case "/save":
            name = arg if arg else _prompt_session_name()
            if name:
                path = save_session(agent.conversation, config["model"], config["work_dir"], name)
                console.print(f"[dim cyan]✓ 保存: {path}[/dim cyan]")
            else:
                console.print("[dim]保存をキャンセルしました。[/dim]")

        case "/load":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]保存済みセッションがありません。[/dim]")
            else:
                _print_sessions(sessions)
                name = arg if arg else Prompt.ask(
                    "[cyan]読み込むセッション名[/cyan]",
                    console=console,
                    default=AUTOSAVE_NAME,
                )
                _load_session_into_agent(agent, name)

        case "/sessions":
            sessions = list_sessions()
            _print_sessions(sessions) if sessions else console.print("[dim]保存済みセッションがありません。[/dim]")

        case "/delete":
            if not arg:
                console.print("[bold red]使い方: /delete <セッション名>[/bold red]")
            elif Confirm.ask(f"セッション '{arg}' を削除しますか？", console=console):
                console.print(
                    f"[dim cyan]✓ 削除しました: {arg}[/dim cyan]"
                    if delete_session(arg)
                    else f"[dim]見つかりません: {arg}[/dim]"
                )

        case "/stats":
            _print_stats(agent)

        case "/cd":
            if not arg:
                console.print("[bold red]使い方: /cd <path>[/bold red]")
            else:
                new_path = Path(arg).expanduser().resolve()
                if new_path.is_dir():
                    agent.work_dir = str(new_path)
                    config["work_dir"] = str(new_path)
                    os.chdir(new_path)
                    console.print(f"[dim cyan]✓ 作業ディレクトリ: {new_path}[/dim cyan]")
                else:
                    console.print(f"[bold red]ディレクトリが存在しません: {arg}[/bold red]")

        case "/pwd":
            console.print(f"[dim]{agent.work_dir}[/dim]")

        case "/model":
            console.print(f"[dim]モデル: {agent.client.model}[/dim]")

        case "/project":
            if not arg:
                console.print(
                    "[bold red]使い方: /project <プロジェクトの説明>[/bold red]\n"
                    "[dim]例: /project Pythonで簡単なTODOアプリを作って[/dim]"
                )
            else:
                run_project(arg, agent.client, config)

        case "/help":
            _print_help()

        case _:
            console.print(f"[bold red]不明なコマンド: {command}[/bold red]  (/help で一覧)")

    return False


# ============================================================
# プロジェクト実行
# ============================================================

def run_project(
    project_description: str,
    client: OpenRouterClient,
    config: dict,
) -> None:
    """
    マルチエージェントプロジェクトを実行する。

    1. オーケストレーターが計画を生成
    2. ユーザーが計画を確認
    3. tmux セッションをセットアップ（利用可能な場合）
    4. エージェントを実行
    5. 結果を表示
    """
    work_dir = config["work_dir"]
    # マルチエージェントのログ・ボードも work_dir/.claw/ 以下に保存する
    sessions_dir = str(Path(work_dir) / ".claw")

    # ── 計画生成 ──────────────────────────────────────────────
    console.print()
    console.print(
        Panel(
            Text.from_markup(
                f"[bold]{project_description}[/bold]\n\n"
                "[dim]オーケストレーターがエージェント割り当て計画を生成しています...[/dim]"
            ),
            title="[bold cyan]🎯 プロジェクト開始[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()

    with console.status("[bold cyan]計画を生成中...[/bold cyan]", spinner="dots"):
        plan = plan_project(client, project_description, work_dir)

    # ── 計画を表示して確認 ───────────────────────────────────
    plan_text = format_plan_display(plan)
    console.print(
        Panel(
            Text(plan_text),
            title="[bold yellow]📋 エージェント計画[/bold yellow]",
            border_style="yellow",
            padding=(0, 2),
        )
    )
    console.print()

    if not Confirm.ask("この計画でプロジェクトを開始しますか？", console=console, default=True):
        console.print("[dim]プロジェクトをキャンセルしました。[/dim]")
        return

    console.print()

    # ── ランナー初期化 + 実行ディレクトリを事前作成 ──────────────
    agent_names = [a.agent_name for a in plan.agents]
    session_name = f"claw_{int(time.time()) % 10000}"
    python_bin = sys.executable

    runner = MultiAgentRunner(
        client=client,
        work_dir=work_dir,
        max_iterations=config["max_iterations"],
        sessions_dir=sessions_dir,
    )

    # tmux セットアップより前にディレクトリを作成して実際のパスを取得する。
    # こうすることで watch.py が正しいログファイルを監視できる。
    log_dir, board_path = runner.prepare_run(plan)

    # ── tmux セットアップ ────────────────────────────────────
    tmux_available = tmux.is_available()
    tmux_session = None

    if tmux_available:
        try:
            tmux_session = tmux.setup_project_session(
                session_name=session_name,
                agent_names=agent_names,
                log_dir=log_dir,
                board_path=board_path,
                work_dir=work_dir,
                python_bin=python_bin,
            )
            console.print(
                f"[dim cyan]✓ tmux セッション '{tmux_session}' をセットアップしました[/dim cyan]"
            )
            if not tmux.in_tmux_session():
                console.print(
                    f"[dim cyan]  アタッチ: [bold]tmux attach -t {tmux_session}[/bold][/dim cyan]"
                )
        except Exception as e:
            logger.warning("tmux セットアップ失敗: %s", e)
            console.print(f"[dim yellow]⚠ tmux セットアップ失敗（継続します）: {e}[/dim yellow]")
    else:
        console.print("[dim]tmux が利用できません（ログはファイルに保存されます）[/dim]")

    console.print()

    # 実行状況を表示するためのステート
    agent_status: dict[str, str] = {name: "待機中" for name in agent_names}
    status_lock = threading.Lock()

    def event_callback(event_type: str, agent_name: str, data) -> None:
        """エージェントイベントをリアルタイムで UI に反映する。"""
        with status_lock:
            match event_type:
                case "agent_start":
                    agent_status[agent_name] = "実行中"
                case "api_start":
                    agent_status[agent_name] = f"思考中 (iter {data})"
                case "tool_call_start":
                    tc = data if isinstance(data, dict) else {}
                    agent_status[agent_name] = f"ツール: {tc.get('name', '?')}"
                case "turn_done":
                    agent_status[agent_name] = f"完了 ({data:.1f}s)"
                case "error":
                    agent_status[agent_name] = f"エラー: {str(data)[:50]}"
                case "max_iterations":
                    agent_status[agent_name] = "上限到達"

    def _make_status_table() -> Table:
        """エージェントステータステーブルを生成する。"""
        table = Table(
            title="[bold cyan]🤖 エージェント実行状況[/bold cyan]",
            box=box.ROUNDED,
            border_style="cyan",
            show_lines=False,
        )
        table.add_column("エージェント", style="bold cyan", no_wrap=True)
        table.add_column("役割", style="dim")
        table.add_column("状態", style="yellow")
        for assignment in plan.agents:
            name = assignment.agent_name
            with status_lock:
                status = agent_status.get(name, "待機中")
            style = (
                "bold green" if "完了" in status
                else "bold red" if "エラー" in status
                else "yellow"
            )
            table.add_row(name, assignment.role, Text(status, style=style))
        return table

    results: dict[str, dict] = {}
    run_done = threading.Event()

    def _run_in_thread() -> None:
        nonlocal results
        try:
            results = runner.execute_run(plan, log_dir, board_path, event_callback=event_callback)
        finally:
            run_done.set()

    run_thread = threading.Thread(target=_run_in_thread, daemon=True)
    run_thread.start()

    # Live UI でステータスを更新
    try:
        with Live(
            _make_status_table(),
            console=console,
            refresh_per_second=4,
            vertical_overflow="visible",
        ) as live:
            while not run_done.wait(timeout=0.3):
                live.update(_make_status_table())
            live.update(_make_status_table())
    except KeyboardInterrupt:
        console.print("\n[dim cyan]プロジェクトを中断しました。[/dim cyan]")
        return

    # ── 実行結果を表示 ──────────────────────────────────────
    console.print()
    _print_project_results(results, plan)

    # 共有ボードのパスを表示する
    _show_board_hint(board_path, tmux_session)


# ============================================================
# メイン REPL ループ
# ============================================================

def run_repl(config: dict) -> None:
    """インタラクティブ REPL を実行する。"""
    # セッションディレクトリを work_dir/.claw/sessions/ に設定
    session_init(config["work_dir"])

    client = create_chat_client(config)
    embedding_client = create_embedding_client(config)
    agent = Agent(
        client=client,
        work_dir=config["work_dir"],
        max_iterations=config["max_iterations"],
        embedding_client=embedding_client,
    )

    _print_welcome(config)
    os.chdir(config["work_dir"])

    # prompt.md が存在すれば通知する
    prompt_md = Path(config["work_dir"]) / "prompt.md"
    if prompt_md.exists():
        console.print(
            f"[dim cyan]📋 prompt.md を読み込みました: [bold]{prompt_md}[/bold][/dim cyan]"
        )
        console.print()

    if autosave_exists():
        _offer_resume(agent)

    model_label = config["model"]
    ctx_window = config.get("context_window", 128_000)

    def _estimate_tokens() -> int:
        """会話の推定トークン数を返す（表示用途のみ）。

        英語: 4文字 ≈ 1トークン
        日本語・CJK: 1文字 ≈ 1トークン
        （ASCII 以外は文字あたりのトークン数が多いため別計算）
        """
        ascii_chars = 0
        non_ascii_chars = 0
        for msg in agent.state.conversation:
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            for ch in content:
                if ord(ch) < 128:
                    ascii_chars += 1
                else:
                    non_ascii_chars += 1
        return ascii_chars // 4 + non_ascii_chars

    def _bottom_toolbar() -> HTML:
        """入力欄の下に表示するステータスバーを生成する。"""
        tokens = _estimate_tokens()
        pct = min(tokens / ctx_window, 1.0)
        bar_width = 14
        filled = round(pct * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        color = "ansired" if pct >= 0.8 else "ansiyellow" if pct >= 0.5 else "ansigreen"
        tokens_str = f"{tokens:,}"
        window_str = f"{ctx_window:,}"
        return HTML(
            f"\n"
            f"  <b>{model_label}</b>"
            f"  <ansibrightblack>  context</ansibrightblack>"
            f"  <{color}>{bar}</{color}>"
            f"  <ansibrightblack>~{tokens_str} / {window_str} tokens</ansibrightblack>"
            f"  <ansibrightblack>  Alt+Enter で改行</ansibrightblack>"
        )

    while True:
        try:
            console.print(Rule(style="dim"))

            # pt_patch_stdout により、Rich の出力がプロンプト行の上に表示される。
            # これにより入力欄が画面の最下部に固定される。
            with pt_patch_stdout(raw=True):
                user_input = _input_session.prompt(
                    _INPUT_PROMPT,
                    bottom_toolbar=_bottom_toolbar,
                    style=_PROMPT_STYLE,
                    multiline=True,
                    key_bindings=_input_kb,
                    prompt_continuation="  ",
                ).strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                if handle_slash_command(user_input, agent, config):
                    break
                continue

            run_agent_turn(
                agent, user_input,
                model=config["model"],
                work_dir=config["work_dir"],
            )

        except KeyboardInterrupt:
            console.print("[dim cyan](Ctrl+C) 終了するには /exit を入力してください。[/dim cyan]")
        except EOFError:
            console.print("[dim]終了します。[/dim]")
            break
        except Exception as e:
            console.print(f"[bold red]REPL エラー: {type(e).__name__}: {e}[/bold red]")
            logger.exception("REPLで予期しないエラー")
