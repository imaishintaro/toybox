"""
ツール実装モジュール。
エージェントが呼び出せる全ツールの定義と実行ロジックを管理する。
"""
import os
import re
import subprocess
from pathlib import Path
from typing import Any


# ============================================================
# ツール定義 (OpenAI function calling 形式)
# ============================================================

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "ファイルの内容を読み取る。行番号付きで返す。"
                "offset と limit で読み取り範囲を指定できる。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "読み取るファイルのパス（絶対または相対）",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "読み取り開始行番号 (1始まり)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "読み取る最大行数",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "ファイルにコンテンツを書き込む（上書き）。存在しない場合は作成する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "書き込み先ファイルのパス",
                    },
                    "content": {
                        "type": "string",
                        "description": "書き込む内容",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "ファイル内の特定文字列を別の文字列に置換する。"
                "old_string はファイル内で一意である必要がある。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "編集するファイルのパス",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "置換対象の文字列（完全一致）",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "置換後の文字列",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "全出現箇所を置換するか (デフォルト: false)",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "bashコマンドを実行し、stdout と stderr を返す。"
                "危険なコマンドには注意すること。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "実行するbashコマンド",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "タイムアウト秒数 (デフォルト: 30)",
                    },
                    "work_dir": {
                        "type": "string",
                        "description": "コマンドの実行ディレクトリ",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "globパターンでファイルを検索し、マッチしたパス一覧を返す。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "globパターン (例: '**/*.py', 'src/*.ts')",
                    },
                    "path": {
                        "type": "string",
                        "description": "検索ベースディレクトリ (デフォルト: 作業ディレクトリ)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "正規表現パターンでファイル内容を検索し、マッチした行を返す。"
                "file_glob でファイル種別をフィルタできる。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "検索する正規表現パターン",
                    },
                    "path": {
                        "type": "string",
                        "description": "検索対象ファイルまたはディレクトリ",
                    },
                    "file_glob": {
                        "type": "string",
                        "description": "ファイルフィルタ用globパターン (例: '*.py')",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "大文字小文字を無視するか (デフォルト: false)",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "マッチ行の前後に表示する行数",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "ディレクトリの内容を一覧表示する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "一覧表示するディレクトリパス (デフォルト: 作業ディレクトリ)",
                    },
                    "show_hidden": {
                        "type": "boolean",
                        "description": "隠しファイルを表示するか (デフォルト: false)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "post_to_board",
            "description": (
                "マルチエージェント共有ボードにメッセージを投稿する。"
                "他のエージェントや進捗状況の共有に使用する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "ボードに投稿するメッセージ（Markdown形式可）",
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "投稿するセクション名 (例: '進捗', '完了', '問題点')。"
                            "省略時は末尾に追記する。"
                        ),
                    },
                },
                "required": ["message"],
            },
        },
    },
]


# ============================================================
# ツール実行関数
# ============================================================

def execute_tool(
    name: str,
    arguments: dict[str, Any],
    work_dir: str = ".",
    board_path: str | None = None,
) -> str:
    """
    ツール名と引数を受け取り実行し、結果文字列を返す。

    Args:
        name: ツール名
        arguments: ツールの引数
        work_dir: デフォルト作業ディレクトリ
        board_path: 共有ボードファイルパス（post_to_board ツール用）

    Returns:
        ツール実行結果の文字列
    """
    try:
        match name:
            case "read_file":
                return _read_file(**arguments)
            case "write_file":
                return _write_file(**arguments)
            case "edit_file":
                return _edit_file(**arguments)
            case "bash":
                return _bash(default_work_dir=work_dir, **arguments)
            case "glob":
                return _glob(base_dir=work_dir, **arguments)
            case "grep":
                return _grep(base_dir=work_dir, **arguments)
            case "list_directory":
                return _list_directory(base_dir=work_dir, **arguments)
            case "post_to_board":
                if not board_path:
                    return "[ERROR] 共有ボードが設定されていません（シングルエージェントモード）"
                return _post_to_board(board_path=board_path, **arguments)
            case _:
                return f"[ERROR] 未知のツール: {name}"
    except Exception as e:
        return f"[ERROR] {name} の実行中にエラーが発生しました: {type(e).__name__}: {e}"


def _read_file(
    file_path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    """ファイルを読み取り、行番号付きで返す。"""
    path = Path(file_path)
    if not path.exists():
        return f"[ERROR] ファイルが存在しません: {file_path}"
    if not path.is_file():
        return f"[ERROR] ディレクトリです（ファイルを指定してください）: {file_path}"

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return f"[ERROR] 読み取り権限がありません: {file_path}"

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    start = (offset - 1) if offset and offset > 0 else 0
    end = (start + limit) if limit else total_lines
    start = max(0, min(start, total_lines))
    end = max(start, min(end, total_lines))

    selected = lines[start:end]
    numbered = "".join(
        f"{i + start + 1:6}\t{line}" for i, line in enumerate(selected)
    )

    header = f"File: {file_path} ({total_lines} lines)"
    if offset or limit:
        header += f" [lines {start + 1}-{end}]"
    return f"{header}\n{'-' * 60}\n{numbered}"


def _write_file(file_path: str, content: str) -> str:
    """ファイルを書き込む（上書き）。"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    line_count = content.count("\n") + (
        1 if content and not content.endswith("\n") else 0
    )
    return f"書き込み完了: {file_path} ({line_count} 行)"


def _edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """ファイル内の文字列を置換する。"""
    path = Path(file_path)
    if not path.exists():
        return f"[ERROR] ファイルが存在しません: {file_path}"

    original = path.read_text(encoding="utf-8")
    count = original.count(old_string)

    if count == 0:
        return f"[ERROR] 対象文字列がファイル内に見つかりません: {file_path}"

    if not replace_all and count > 1:
        return (
            f"[ERROR] 対象文字列が {count} 箇所存在します。"
            " replace_all=true を指定するか、より具体的な文字列を指定してください。"
        )

    if replace_all:
        new_content = original.replace(old_string, new_string)
        replaced = count
    else:
        new_content = original.replace(old_string, new_string, 1)
        replaced = 1

    path.write_text(new_content, encoding="utf-8")
    return f"編集完了: {file_path} ({replaced} 箇所置換)"


def _bash(
    command: str,
    timeout: int = 30,
    work_dir: str | None = None,
    default_work_dir: str = ".",
) -> str:
    """bashコマンドを実行して結果を返す。"""
    cwd = work_dir or default_work_dir
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            executable="/bin/bash",
        )
        output_parts = []
        if result.stdout:
            output_parts.append(f"[stdout]\n{result.stdout.rstrip()}")
        if result.stderr:
            output_parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        output_parts.append(f"[exit code] {result.returncode}")
        return "\n".join(output_parts) if output_parts else "(出力なし)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] タイムアウト ({timeout}秒)"


def _glob(pattern: str, path: str | None = None, base_dir: str = ".") -> str:
    """globパターンでファイルを検索する。"""
    search_dir = Path(path or base_dir)
    if not search_dir.exists():
        return f"[ERROR] ディレクトリが存在しません: {search_dir}"

    matches = sorted(search_dir.glob(pattern))
    if not matches:
        return f"マッチなし: pattern='{pattern}' in '{search_dir}'"

    lines = [str(m) for m in matches]
    return f"{len(lines)} 件マッチ:\n" + "\n".join(lines)


def _grep(
    pattern: str,
    path: str | None = None,
    file_glob: str | None = None,
    case_insensitive: bool = False,
    context_lines: int = 0,
    base_dir: str = ".",
) -> str:
    """正規表現でファイル内容を検索する。"""
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"[ERROR] 無効な正規表現: {e}"

    search_path = Path(path or base_dir)
    results: list[str] = []

    def search_file(file_path: Path) -> None:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, IsADirectoryError):
            return
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if regex.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                for j in range(start, end):
                    sep = ":" if j == i else "-"
                    results.append(f"{file_path}:{j + 1}{sep}{lines[j]}")
                if context_lines > 0:
                    results.append("--")

    if search_path.is_file():
        search_file(search_path)
    elif search_path.is_dir():
        target_files = (
            sorted(search_path.rglob(file_glob))
            if file_glob
            else sorted(f for f in search_path.rglob("*") if f.is_file())
        )
        for f in target_files:
            search_file(f)
    else:
        return f"[ERROR] パスが存在しません: {search_path}"

    if not results:
        return f"マッチなし: pattern='{pattern}'"

    max_lines = 500
    if len(results) > max_lines:
        truncated = len(results) - max_lines
        results = results[:max_lines]
        results.append(f"... (さらに {truncated} 行省略)")

    return "\n".join(results)


def _list_directory(
    path: str | None = None,
    show_hidden: bool = False,
    base_dir: str = ".",
) -> str:
    """ディレクトリ内容を一覧表示する。"""
    target = Path(path or base_dir)
    if not target.exists():
        return f"[ERROR] パスが存在しません: {target}"
    if not target.is_dir():
        return f"[ERROR] ディレクトリではありません: {target}"

    entries = sorted(
        target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())
    )
    lines = [f"ディレクトリ: {target.resolve()}", ""]
    count = 0

    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        count += 1
        if entry.is_dir():
            lines.append(f"  📁 {entry.name}/")
        elif entry.is_file():
            size_str = _format_size(entry.stat().st_size)
            lines.append(f"  📄 {entry.name} ({size_str})")
        else:
            lines.append(f"  🔗 {entry.name}")

    lines.append(f"\n合計: {count} 件")
    return "\n".join(lines)


def _format_size(size: int) -> str:
    """ファイルサイズを人間に読みやすい形式に変換する。"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.0f}{unit}"
        size //= 1024
    return f"{size:.0f}TB"


def _post_to_board(
    board_path: str,
    message: str,
    section: str | None = None,
) -> str:
    """共有ボードにメッセージを追記する。"""
    import datetime

    path = Path(board_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%H:%M:%S")

    if section:
        entry = f"\n### {section} [{timestamp}]\n{message}\n"
    else:
        entry = f"\n---\n**[{timestamp}]**\n{message}\n"

    with path.open("a", encoding="utf-8") as f:
        f.write(entry)

    return f"共有ボードに投稿しました: {board_path}"
