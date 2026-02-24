"""
claw - Claude-Like Agent Workflow
OpenRouterを使ったClaude Codeライクなターミナルエージェント

使い方:
  python .app/src/main.py [--debug] [--work-dir <path>]
"""
import sys
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from func_config import load_config
from func_repl import run_repl
from func_ui import console


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
