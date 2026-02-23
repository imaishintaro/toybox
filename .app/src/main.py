"""
claw - Claude-Like Agent Workflow
OpenRouterを使ったClaude Codeライクなターミナルエージェント

修正済みバグ:
  - text_bufferがエラー時にクリアされない → エラー/例外時に必ずクリア
  - スピナーが更新頻度でボトルネック → text_delta の Live 更新を間引き
  - Live が例外で残存 → with ブロックで確実にクリーンアップ

使い方:
  python .app/src/main.py [--debug] [--work-dir <path>]
"""
import sys
import logging
import os
import argparse
import time
import threading
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.rule import Rule
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner
from rich import box

sys.path.insert(0, str(Path(__file__).parent))

from func_config import load_config
from func_openrouter import OpenRouterClient
from func_agent import Agent, ToolCallResult
from func_session import (
    save_session, load_session, list_sessions,
    delete_session, autosave_exists, AUTOSAVE_NAME,
)
from func_orchestrator import plan_project, format_plan_display, ProjectPlan
from func_multi_agent import MultiAgentRunner
import func_tmux as tmux

console = Console()
logger = logging.getLogger(__name__)

# Live 更新の間引き設定（毎チャンク更新するとボトルネックになる）
_LIVE_UPDATE_INTERVAL = 0.05  # 秒: この間隔より短い更新はスキップ


# ============================================================
# パネル生成ヘルパー
# ============================================================

def _text_panel(text: str, streaming: bool = False) -> Panel:
    """アシスタントテキストパネルを生成する。"""
    if streaming:
        content = Text(text + "▋")
    else:
        try:
            content = Markdown(text)
        except Exception:
            content = Text(text)
    return Panel(
        content,
        title="[bold green]🤖 Assistant[/bold green]",
        border_style="green",
        padding=(0, 1),
    )


def _tool_call_panel(tc: dict) -> Panel:
    """ツール呼び出しパネルを生成する。"""
    import json as _json

    body = Text()
    body.append(f"  {tc['name']}\n", style="bold yellow")

    try:
        args = _json.loads(tc.get("arguments", "{}"))
    except (_json.JSONDecodeError, TypeError):
        args = {}

    for key, value in args.items():
        if isinstance(value, str):
            val_str = value
        else:
            val_str = repr(value)
        if len(val_str) > 120:
            val_str = val_str[:117] + "..."
        body.append(f"  {key}", style="cyan")
        body.append(" = ", style="dim")
        body.append(val_str + "\n", style="dim yellow")

    return Panel(
        body,
        title="[bold yellow]🔧 Tool Call[/bold yellow]",
        border_style="yellow",
        padding=(0, 1),
    )


def _tool_result_panel(result: ToolCallResult) -> Panel:
    """ツール結果パネルを生成する。"""
    output = result.result
    is_error = output.startswith("[ERROR]")

    max_chars = 3000
    if len(output) > max_chars:
        omitted = len(output) - max_chars
        output = output[:max_chars] + f"\n\n... さらに {omitted} 文字省略"

    return Panel(
        Text(output, style="bold red" if is_error else ""),
        title=(
            f"[bold red]❌ Error: {result.name}[/bold red]"
            if is_error
            else f"[bold white]✅ Result: {result.name}[/bold white]"
        ),
        border_style="red" if is_error else "dim",
        padding=(0, 1),
    )


def _spinner(msg: str, iteration: int, tool_count: int, elapsed: float) -> Spinner:
    """ステータススピナーを生成する。"""
    status = Text()
    status.append(f" {msg}", style="dim")
    status.append(
        f"  iter {iteration}  tools {tool_count}  {elapsed:.1f}s",
        style="dim cyan",
    )
    return Spinner("dots", text=status)


# ============================================================
# エージェントターンの実行（リアルタイムUI）
# ============================================================

def run_agent_turn(
    agent: Agent,
    user_input: str,
    model: str,
    work_dir: str,
    auto_save: bool = True,
) -> None:
    """
    1ターンのエージェントループをリアルタイムUIで実行する。

    Live を使って現在進行中の状態を表示し、
    確定したパネルは console.print() で永続表示する。

    バグ修正:
      - text_buffer はエラー/例外時にも必ず印刷してクリアする
      - Live はコンテキストマネージャで確実にクリーンアップする
      - text_delta 更新は _LIVE_UPDATE_INTERVAL で間引いてボトルネックを防ぐ
    """
    start_time = time.time()
    text_buffer = ""          # ストリーミング中のテキスト蓄積
    iteration = 0
    tool_count = 0
    last_live_update = 0.0   # 最終 Live 更新タイムスタンプ

    def elapsed() -> float:
        return time.time() - start_time

    def flush_text_buffer() -> None:
        """text_buffer の内容を永続パネルとして表示しバッファをクリアする。"""
        nonlocal text_buffer
        if text_buffer:
            console.print(_text_panel(text_buffer, streaming=False))
            text_buffer = ""

    console.print()

    with Live(
        _spinner("接続中...", 0, 0, 0),
        console=console,
        refresh_per_second=15,
        vertical_overflow="visible",
    ) as live:
        try:
            for event_type, data in agent.stream_run(user_input):
                match event_type:

                    case "api_start":
                        iteration = data
                        tool_count = agent.stats["tool_call_count"]
                        live.update(_spinner("思考中...", iteration, tool_count, elapsed()))
                        last_live_update = time.time()

                    case "text_delta":
                        text_buffer += data
                        # 間引き: 前回更新から _LIVE_UPDATE_INTERVAL 秒経過した場合のみ更新
                        now = time.time()
                        if now - last_live_update >= _LIVE_UPDATE_INTERVAL:
                            live.update(_text_panel(text_buffer, streaming=True))
                            last_live_update = now

                    case "text_done":
                        # text_done の data を正として使う（openrouter側の蓄積値）
                        text_buffer = data or text_buffer
                        flush_text_buffer()
                        tool_count = agent.stats["tool_call_count"]
                        live.update(
                            _spinner("次のアクションを考えています...", iteration, tool_count, elapsed())
                        )

                    case "tool_call_start":
                        tc = data
                        flush_text_buffer()   # テキストが残っていれば先に確定
                        console.print(_tool_call_panel(tc))
                        live.update(_spinner(f"⚙ {tc['name']} 実行中...", iteration, tool_count, elapsed()))

                    case "tool_result":
                        result: ToolCallResult = data
                        tool_count += 1
                        console.print(_tool_result_panel(result))
                        live.update(_spinner("結果を分析中...", iteration, tool_count, elapsed()))

                    case "max_iterations":
                        console.print(
                            f"[bold yellow]⚠ 最大反復回数 ({data}) に達しました。"
                            " タスクが完了していない可能性があります。[/bold yellow]"
                        )

                    case "error":
                        # エラー時も text_buffer を印刷してから表示をクリア
                        flush_text_buffer()
                        console.print(f"[bold red]✗ エラー: {data}[/bold red]")
                        live.update(Text(""))

                    case "turn_done":
                        total = data
                        flush_text_buffer()  # 念のため残っていれば印刷
                        live.update(
                            Text(
                                f"  ✓ 完了  {total:.1f}s"
                                f"  (iter {iteration}  tools {tool_count})",
                                style="dim green",
                            )
                        )

        except KeyboardInterrupt:
            flush_text_buffer()
            live.update(Text(""))
            console.print("\n[dim cyan]処理を中断しました。[/dim cyan]")

        except Exception as e:
            flush_text_buffer()
            live.update(Text(""))
            console.print(f"[bold red]予期しないエラー: {type(e).__name__}: {e}[/bold red]")
            logger.exception("エージェントターン中に予期しないエラー")

    # 自動保存
    if auto_save and agent.conversation:
        try:
            save_session(agent.conversation, model, work_dir)
            logger.debug("自動保存完了")
        except Exception as e:
            logger.warning("自動保存失敗: %s", e)


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
# UI ヘルパー
# ============================================================

def _print_sessions(sessions: list[dict]) -> None:
    table = Table(title="保存済みセッション", box=box.ROUNDED, border_style="cyan", show_lines=False)
    table.add_column("名前", style="bold cyan", no_wrap=True)
    table.add_column("保存日時", style="dim")
    table.add_column("メッセージ", justify="right")
    table.add_column("モデル", style="dim")
    for s in sessions:
        name = f"[bold]{s['name']}[/bold]" if s["name"] == AUTOSAVE_NAME else s["name"]
        table.add_row(name, s["saved_at"], str(s["message_count"]), s["model"])
    console.print(table)
    console.print()


def _print_stats(agent: Agent) -> None:
    stats = agent.stats
    table = Table(title="実行統計", box=box.ROUNDED, border_style="cyan")
    table.add_column("項目", style="bold")
    table.add_column("値", style="cyan")
    table.add_row("総イテレーション数", str(stats["iteration_count"]))
    table.add_row("総ツール呼び出し数", str(stats["tool_call_count"]))
    table.add_row("会話メッセージ数", str(stats["conversation_length"]))
    console.print(table)
    console.print()


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
    sessions_dir = str(Path(__file__).parent.parent / "sessions")

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

    # ── tmux セットアップ ────────────────────────────────────
    agent_names = [a.agent_name for a in plan.agents]
    session_name = f"claw_{int(time.time()) % 10000}"
    python_bin = sys.executable

    # ダミーの log_dir と board_path（実際の値は MultiAgentRunner が生成する）
    # tmux は先に作るが、ログファイルは実行時に作られるので watch.py が待機する
    import tempfile
    tmp_log_dir = str(Path(sessions_dir) / "tmp_logs")
    tmp_board = str(Path(sessions_dir) / "tmp_board.md")
    Path(tmp_log_dir).mkdir(parents=True, exist_ok=True)

    tmux_available = tmux.is_available()
    tmux_session = None

    if tmux_available:
        try:
            tmux_session = tmux.setup_project_session(
                session_name=session_name,
                agent_names=agent_names,
                log_dir=tmp_log_dir,
                board_path=tmp_board,
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

    # ── エージェント実行（Live UI 付き） ───────────────────────
    runner = MultiAgentRunner(
        client=client,
        work_dir=work_dir,
        max_iterations=config["max_iterations"],
        sessions_dir=sessions_dir,
    )

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
            results = runner.run(plan, event_callback=event_callback)
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

    # ボードファイルのパスを出力（tmux セッションに表示されるはず）
    if results:
        # 最初の結果から board_path を推定（現状は summary 情報なし → runner から取る方法なし）
        # 代わりに sessions_dir から最新ディレクトリを探す
        _show_board_hint(sessions_dir, tmux_session)


def _print_project_results(results: dict[str, dict], plan: ProjectPlan) -> None:
    """プロジェクト実行結果をコンソールに表示する。"""
    all_success = all(r["success"] for r in results.values())

    title_style = "bold green" if all_success else "bold yellow"
    title_icon = "✅" if all_success else "⚠"

    table = Table(
        title=f"[{title_style}]{title_icon} プロジェクト完了[/{title_style}]",
        box=box.ROUNDED,
        border_style="green" if all_success else "yellow",
    )
    table.add_column("エージェント", style="bold", no_wrap=True)
    table.add_column("役割")
    table.add_column("結果", justify="center")
    table.add_column("時間", justify="right")
    table.add_column("反復/ツール", justify="right")

    for assignment in plan.agents:
        name = assignment.agent_name
        r = results.get(name, {})
        success = r.get("success", False)
        elapsed = r.get("elapsed", 0)
        stats = r.get("stats", {})
        status_text = Text("✅ 成功" if success else "❌ 失敗")
        status_text.stylize("bold green" if success else "bold red")
        table.add_row(
            name,
            assignment.role,
            status_text,
            f"{elapsed:.1f}s",
            f"{stats.get('iteration_count', 0)} / {stats.get('tool_call_count', 0)}",
        )

    console.print(table)
    console.print()


def _show_board_hint(sessions_dir: str, tmux_session: str | None) -> None:
    """共有ボードのパスヒントを表示する。"""
    sessions_path = Path(sessions_dir)
    if not sessions_path.exists():
        return

    # 最新の project_* ディレクトリを探す
    project_dirs = sorted(
        [d for d in sessions_path.iterdir() if d.is_dir() and d.name.startswith("project_")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not project_dirs:
        return

    board_path = project_dirs[0] / "board.md"
    if board_path.exists():
        console.print(
            f"[dim cyan]共有ボード: [bold]{board_path}[/bold][/dim cyan]"
        )
        if tmux_session:
            console.print(
                f"[dim]  tmux セッション [bold]{tmux_session}[/bold] の shared ウィンドウで確認できます[/dim]"
            )
        else:
            console.print(f"[dim]  cat {board_path}[/dim]")
    console.print()


def _print_help() -> None:
    table = Table(title="利用可能なコマンド", box=box.ROUNDED, border_style="cyan")
    table.add_column("コマンド", style="bold cyan", no_wrap=True)
    table.add_column("説明")
    rows = [
        ("/project <説明>", "マルチエージェントでプロジェクトを実行する"),
        ("/exit, /quit, /q", "clawを終了する"),
        ("/reset, /clear", "会話履歴をリセットする"),
        ("/save [名前]", "会話をセッションファイルに保存する"),
        ("/load [名前]", "セッションを読み込んで会話を再開する"),
        ("/sessions", "保存済みセッション一覧を表示する"),
        ("/delete <名前>", "セッションを削除する"),
        ("/stats", "実行統計を表示する"),
        ("/cd <path>", "作業ディレクトリを変更する"),
        ("/pwd", "現在の作業ディレクトリを表示する"),
        ("/model", "使用中のモデルを表示する"),
        ("/help", "このヘルプを表示する"),
        ("Ctrl+C", "現在の処理を中断する"),
    ]
    for cmd, desc in rows:
        table.add_row(cmd, desc)
    console.print(table)
    console.print()


def _prompt_session_name() -> str:
    return Prompt.ask("[cyan]セッション名 (空でキャンセル)[/cyan]", console=console, default="").strip()


def _load_session_into_agent(agent: Agent, name: str) -> None:
    data = load_session(name)
    if not data:
        console.print(f"[bold red]セッションが見つかりません: {name}[/bold red]")
        return
    agent.reset_conversation()
    agent.load_conversation(data["conversation"])
    console.print(
        f"[dim cyan]✓ '{name}' を読み込みました ({data['message_count']} メッセージ)[/dim cyan]"
    )


def _print_welcome(config: dict) -> None:
    console.print()
    console.print(
        Panel(
            Text.from_markup(
                "[bold cyan]claw[/bold cyan]  Claude-Like Agent Workflow\n\n"
                f"  Model   [bold]{config['model']}[/bold]\n"
                f"  WorkDir [bold]{config['work_dir']}[/bold]\n"
                f"  MaxIter [bold]{config['max_iterations']}[/bold]\n"
                f"  Env     [dim]{config['env_path']}[/dim]\n\n"
                "[dim]/help でコマンド一覧  /exit で終了[/dim]"
            ),
            title="[bold cyan]✨ claw[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()


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
# メイン REPL ループ
# ============================================================

def run_repl(config: dict) -> None:
    """インタラクティブ REPL を実行する。"""
    client = OpenRouterClient(api_key=config["api_key"], model=config["model"])
    agent = Agent(
        client=client,
        work_dir=config["work_dir"],
        max_iterations=config["max_iterations"],
    )

    _print_welcome(config)
    os.chdir(config["work_dir"])

    if autosave_exists():
        _offer_resume(agent)

    while True:
        try:
            console.print(Rule(style="dim"))
            user_input = Prompt.ask("[bold cyan]You[/bold cyan]", console=console).strip()

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
            console.print("\n[dim cyan](Ctrl+C) 終了するには /exit を入力してください。[/dim cyan]")
        except EOFError:
            console.print("\n[dim]終了します。[/dim]")
            break
        except Exception as e:
            console.print(f"[bold red]REPL エラー: {type(e).__name__}: {e}[/bold red]")
            logger.exception("REPLで予期しないエラー")


# ============================================================
# エントリーポイント
# ============================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="claw - Claude-Like Agent Workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例:\n  python .app/src/main.py\n  python .app/src/main.py --debug\n",
    )
    parser.add_argument("--debug", action="store_true", help="デバッグログを有効にする")
    parser.add_argument("--work-dir", metavar="PATH", help="作業ディレクトリ")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if args.debug:
        console.print("[dim]デバッグモードが有効です[/dim]")

    config = load_config()

    if args.work_dir:
        work_dir = Path(args.work_dir).expanduser().resolve()
        if not work_dir.is_dir():
            console.print(f"[bold red]ERROR: ディレクトリが存在しません: {work_dir}[/bold red]")
            sys.exit(1)
        config["work_dir"] = str(work_dir)

    run_repl(config)


if __name__ == "__main__":
    main()
