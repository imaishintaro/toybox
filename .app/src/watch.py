"""
ファイル監視スクリプト（tmux ペイン用）。

tmux ウィンドウでエージェントログや共有ボードをリアルタイム表示する。

使い方:
    python watch.py <file_path> [title]

    file_path: 監視するファイルパス
    title:     ウィンドウタイトル（省略可）
"""
import sys
import time
import os
from pathlib import Path


def _try_import_rich() -> bool:
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


def watch_plain(file_path: str, title: str) -> None:
    """Rich なしのシンプルな tail -f 相当。"""
    print(f"=== {title} ===")
    print(f"監視中: {file_path}")
    print("-" * 60)

    path = Path(file_path)
    last_size = 0   # バイトサイズ（変更検知用）
    last_pos = 0    # 文字数（スライス用: マルチバイト文字に対応）

    while True:
        try:
            if not path.exists():
                time.sleep(0.5)
                continue

            size = path.stat().st_size
            if size > last_size:
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                new_content = content[last_pos:]
                if new_content:
                    print(new_content, end="", flush=True)
                last_pos = len(content)
                last_size = size
            time.sleep(0.5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n[エラー] {e}", flush=True)
            time.sleep(1)


def watch_rich(file_path: str, title: str) -> None:
    """Rich を使ったカラー表示付きファイル監視。"""
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.rule import Rule

    console = Console()
    path = Path(file_path)

    console.clear()
    console.print(
        Panel(
            f"[dim]{file_path}[/dim]",
            title=f"[bold cyan]{title}[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        )
    )
    console.print(Rule(style="dim"))

    # ファイルが存在するまで待機（1回だけ表示）
    if not path.exists():
        console.print(f"[dim]エージェント起動待機中...[/dim]")
        while not path.exists():
            time.sleep(0.5)

    last_size = 0   # バイトサイズ（変更検知用）
    last_pos = 0    # 文字数（スライス用: マルチバイト文字に対応）

    # ボードファイル（.md 拡張子）か判定
    is_board = file_path.endswith(".md")

    try:
        while True:
            if not path.exists():
                time.sleep(0.5)
                continue

            size = path.stat().st_size
            if size != last_size:
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                if is_board:
                    # ボードファイルはクリアして全表示
                    console.clear()
                    console.print(
                        Panel(
                            f"[dim]{file_path}[/dim]",
                            title=f"[bold cyan]{title}[/bold cyan]",
                            border_style="cyan",
                            padding=(0, 1),
                        )
                    )
                    console.print(Rule(style="dim"))
                    try:
                        from rich.markdown import Markdown
                        console.print(Markdown(content))
                    except Exception:
                        console.print(content)
                else:
                    # ログファイルは差分だけ追記表示（文字数でスライス）
                    new_content = content[last_pos:]
                    lines = new_content.splitlines()
                    for line in lines:
                        _print_log_line(console, line)
                    last_pos = len(content)

                last_size = size

            time.sleep(0.5)

    except KeyboardInterrupt:
        console.print("\n[dim cyan]監視を終了しました。[/dim cyan]")


def _print_log_line(console, line: str) -> None:
    """ログ行を解析してカラー表示する。"""
    from rich.text import Text

    line = line.rstrip()
    if not line:
        return

    text = Text()

    # ログレベルでカラー付け
    if '"level": "ERROR"' in line or "[ERROR]" in line:
        text.append(line, style="bold red")
    elif '"level": "WARNING"' in line or "[WARNING]" in line:
        text.append(line, style="yellow")
    elif '"type": "tool_call"' in line or "tool_call" in line:
        text.append(line, style="bold yellow")
    elif '"type": "tool_result"' in line or "tool_result" in line:
        text.append(line, style="dim white")
    elif '"type": "text"' in line:
        text.append(line, style="green")
    elif '"type": "done"' in line or "done" in line.lower():
        text.append(line, style="bold green")
    elif '"type": "start"' in line:
        text.append(line, style="bold cyan")
    else:
        text.append(line, style="dim")

    console.print(text)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("使い方: python watch.py <file_path> [title]")
        sys.exit(1)

    file_path = args[0]
    title = args[1] if len(args) > 1 else Path(file_path).name

    if _try_import_rich():
        watch_rich(file_path, title)
    else:
        watch_plain(file_path, title)


if __name__ == "__main__":
    main()
