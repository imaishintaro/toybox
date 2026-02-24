"""claw - UI パネル生成 + エージェントターン実行

ウェルカム画面、パネル描画、スピナー、run_agent_turn() など
Rich ベースの表示ロジックをまとめたモジュール。
"""
import time
import shutil
import logging
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner
from rich import box

from func_agent import Agent, ToolCallResult
from func_session import save_session
from func_orchestrator import ProjectPlan

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

    # ── ダイナミックレンダラー ───────────────────────────────────────
    # Live に渡すレンダラブル。__rich_console__ は refresh のたびに呼ばれるため、
    # spinner モード時は elapsed() を毎回再計算して秒数がリアルタイムに進む。
    class _Renderer:
        def __init__(self) -> None:
            self._static: object = Text("")
            self._is_spinner: bool = True
            self.msg: str = "接続中..."

        def set_spinner(self, msg: str) -> None:
            """スピナーモードに切り替え（秒数は refresh ごとに自動更新）。"""
            self.msg = msg
            self._is_spinner = True

        def set_static(self, renderable: object) -> None:
            """静的レンダラブルを表示（テキストパネル / 空白）。"""
            self._static = renderable
            self._is_spinner = False

        def __rich_console__(self, console, options):  # noqa: ANN001
            if self._is_spinner:
                yield _spinner(self.msg, iteration, tool_count, elapsed())
            else:
                yield self._static

    renderer = _Renderer()

    def flush_text_buffer() -> None:
        """text_buffer の内容を永続パネルとして表示しバッファをクリアする。

        console.print() の前に Live をクリアすることで、バックグラウンドの
        リフレッシュスレッドとの出力競合によるパネル崩れを防ぐ。
        """
        nonlocal text_buffer
        if text_buffer:
            renderer.set_static(Text(""))
            live.refresh()
            console.print(_text_panel(text_buffer, streaming=False))
            text_buffer = ""

    console.print()

    # ストリーミングプレビューで表示する最大行数（端末高さ - 余白）
    # これにより Live エリアが端末をはみ出さず、カーソル位置が安定する。
    _PREVIEW_LINES = max(10, shutil.get_terminal_size(fallback=(80, 24)).lines - 6)

    with Live(
        renderer,
        console=console,
        refresh_per_second=8,
        vertical_overflow="crop",
    ) as live:
        try:
            for event_type, data in agent.stream_run(user_input):
                match event_type:

                    case "api_start":
                        iteration = data
                        tool_count = agent.stats["tool_call_count"]
                        renderer.set_spinner("思考中...")

                    case "text_delta":
                        text_buffer += data
                        # 間引き: 前回更新から _LIVE_UPDATE_INTERVAL 秒経過した場合のみ更新
                        now = time.time()
                        if now - last_live_update >= _LIVE_UPDATE_INTERVAL:
                            # 末尾 _PREVIEW_LINES 行のみ表示して Live エリアを端末高さ以内に収める。
                            # text_done 後に完全なパネルを console.print() で出力する。
                            lines = text_buffer.split("\n")
                            if len(lines) > _PREVIEW_LINES:
                                preview = (
                                    f"[dim]…({len(lines) - _PREVIEW_LINES} 行省略)[/dim]\n"
                                    + "\n".join(lines[-_PREVIEW_LINES:])
                                )
                            else:
                                preview = text_buffer
                            renderer.set_static(_text_panel(preview, streaming=True))
                            last_live_update = now

                    case "text_done":
                        # text_done の data を正として使う（openrouter側の蓄積値）
                        text_buffer = data or text_buffer
                        flush_text_buffer()
                        tool_count = agent.stats["tool_call_count"]
                        renderer.set_spinner("次のアクションを考えています...")

                    case "tool_call_start":
                        tc = data
                        flush_text_buffer()   # テキストが残っていれば先に確定
                        renderer.set_static(Text(""))
                        live.refresh()
                        console.print(_tool_call_panel(tc))
                        renderer.set_spinner(f"⚙ {tc['name']} 実行中...")

                    case "tool_result":
                        result: ToolCallResult = data
                        tool_count += 1
                        renderer.set_static(Text(""))
                        live.refresh()
                        console.print(_tool_result_panel(result))
                        renderer.set_spinner("結果を分析中...")

                    case "max_iterations":
                        console.print(
                            f"[bold yellow]⚠ 最大反復回数 ({data}) に達しました。"
                            " タスクが完了していない可能性があります。[/bold yellow]"
                        )

                    case "error":
                        # エラー時も text_buffer を印刷してから表示をクリア
                        flush_text_buffer()
                        renderer.set_static(Text(""))
                        live.refresh()
                        console.print(f"[bold red]✗ エラー: {data}[/bold red]")

                    case "turn_done":
                        total = data
                        flush_text_buffer()  # 念のため残っていれば印刷
                        renderer.set_static(Text(""))
                        live.refresh()
                        console.print(
                            Text(
                                f"  ✓ 完了  {total:.1f}s"
                                f"  (iter {iteration}  tools {tool_count})",
                                style="dim green",
                            )
                        )

        except KeyboardInterrupt:
            flush_text_buffer()
            renderer.set_static(Text(""))
            console.print("[dim cyan]処理を中断しました。[/dim cyan]")

        except Exception as e:
            flush_text_buffer()
            renderer.set_static(Text(""))
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
# UI ヘルパー
# ============================================================

def _print_welcome(config: dict) -> None:
    """タイプライター風アニメーションでウェルカムメッセージを表示する。"""
    provider_label = "Azure OpenAI" if config.get("provider") == "azure" else "OpenRouter"
    emb_info = f"  ({config['embedding_model']})" if config.get("embedding_model") else ""

    segments = [
        ("claw", "bold cyan"),
        ("  Claude-Like Agent Workflow\n\n", ""),
        ("  Provider ", "dim"),
        (f"{provider_label}\n", "bold"),
        ("  Model   ", "dim"),
        (f"{config['model']}\n", "bold"),
        ("  WorkDir ", "dim"),
        (f"{config['work_dir']}\n", "bold"),
        ("  MaxIter ", "dim"),
        (f"{config['max_iterations']}\n", "bold"),
        ("  Compress ", "dim"),
        (f"{'有効' + emb_info if config.get('embedding_model') else '無効'}\n\n", "bold"),
        ("/help でコマンド一覧  /exit で終了", "dim"),
    ]

    def _welcome_panel(content: Text) -> Panel:
        return Panel(
            content,
            title="[bold cyan]✨ claw[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        )

    console.print()
    revealed = Text()

    with Live(_welcome_panel(revealed), console=console, refresh_per_second=30) as live:
        for text, style in segments:
            for char in text:
                revealed.append(char, style=style or None)
                live.update(_welcome_panel(revealed))
                time.sleep(0.012)

    console.print()


def _print_sessions(sessions: list[dict]) -> None:
    """保存済みセッション一覧をテーブル表示する。"""
    from func_session import AUTOSAVE_NAME

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
    """実行統計をテーブル表示する。"""
    stats = agent.stats
    table = Table(title="実行統計", box=box.ROUNDED, border_style="cyan")
    table.add_column("項目", style="bold")
    table.add_column("値", style="cyan")
    table.add_row("総イテレーション数", str(stats["iteration_count"]))
    table.add_row("総ツール呼び出し数", str(stats["tool_call_count"]))
    table.add_row("会話メッセージ数", str(stats["conversation_length"]))
    console.print(table)
    console.print()


def _print_help() -> None:
    """利用可能なコマンド一覧を表示する。"""
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


def _show_board_hint(board_path: str, tmux_session: str | None) -> None:
    """共有ボードのパスヒントを表示する。"""
    if not Path(board_path).exists():
        return

    console.print(f"[dim cyan]共有ボード: [bold]{board_path}[/bold][/dim cyan]")
    if tmux_session:
        console.print(
            f"[dim]  tmux セッション [bold]{tmux_session}[/bold] の shared ウィンドウで確認できます[/dim]"
        )
    else:
        console.print(f"[dim]  cat {board_path}[/dim]")
    console.print()
