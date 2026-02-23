"""
エージェントループモジュール。

stream_run() ジェネレータがイベントを yield し、
UIレイヤー (main.py) がそれを消費してリアルタイム表示を行う。

修正済みバグ:
  - エラー発生時に不完全なアシスタントメッセージが履歴に追加される問題
  - エラー後の turn_done が確実に yield されるよう保証

発行イベント一覧:
  ("api_start",       iteration: int)           API呼び出し開始
  ("text_delta",      chunk: str)               テキストチャンク（ストリーミング中）
  ("text_done",       full_text: str)           テキスト受信完了
  ("tool_call_start", tc: dict)                ツール実行開始 {id, name, arguments}
  ("tool_result",     result: ToolCallResult)   ツール実行完了
  ("turn_done",       elapsed: float)           ターン完了（常に最後に発行）
  ("max_iterations",  max: int)                上限到達
  ("error",           msg: str)                エラー発生
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Generator, Any

from func_openrouter import OpenRouterClient
from func_tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

# ============================================================
# システムプロンプト（エージェント精度に直結）
# ============================================================
SYSTEM_PROMPT = """You are an expert software engineering assistant. Always respond in Japanese.

## CORE PRINCIPLES

### 1. Explore before acting
Always survey the project before writing code:
- list_directory → understand structure
- glob → find files by pattern ("**/*.py" etc.)
- grep → locate functions, imports, patterns
- read_file → understand existing code before modifying it

### 2. Read before editing — this is mandatory
NEVER modify a file without reading it first with read_file.
Skipping this step causes hard-to-detect bugs.

### 3. edit_file for existing files, write_file for new files only
- edit_file: does precise string replacement — safe and auditable
  - old_string MUST be unique in the file
  - If not unique, include 2-3 more surrounding lines to make it unique
  - If old_string is not found, read the file again to check the actual content
- write_file: overwrites the ENTIRE file — only use when creating a new file

### 4. Verify your changes
After editing: re-read the changed section with read_file to confirm correctness.
After code changes: run with bash to check for syntax errors.

### 5. Efficient tool chaining
Think ahead. Plan multiple sequential tool calls rather than one at a time.
Adapt when you discover unexpected things; do not rigidly follow an initial plan.

## WHEN THINGS GO WRONG
- Tool error → diagnose root cause, try a different approach, don't give up
- bash exit ≠ 0 → read stderr carefully, fix the actual problem
- File not found → verify path with list_directory or glob first
- edit_file "not unique" → expand old_string to include more surrounding context
- edit_file "not found" → read the file first to see the actual content
- Stuck after 2 failed attempts → clearly explain the problem to the user

## TOOL USAGE TIPS
- read_file: use offset/limit for large files (read only what's needed)
- grep: set file_glob (e.g. "*.py") to avoid binary files
- bash: set timeout=60 for compilation/tests; timeout=10 for quick checks
- glob: "**/" prefix for recursive search (e.g. "**/*.py")
- list_directory: show_hidden=true to see dot files

## COMMUNICATION STYLE
- Brief (1 line) progress note before each major action
- Detailed summary when the full task is complete
- Confirm with user BEFORE: deleting files, major rewrites, irreversible operations
- On error: state what happened, why, what you'll try next
"""

# コンテキストウィンドウ管理
MAX_CONVERSATION_MESSAGES = 80
KEEP_RECENT_MESSAGES = 60


# ============================================================
# データクラス
# ============================================================

@dataclass
class AgentState:
    """エージェントの実行状態。"""

    conversation: list[dict] = field(default_factory=list)
    iteration_count: int = 0
    tool_call_count: int = 0


@dataclass
class ToolCallResult:
    """ツール呼び出し結果。"""

    tool_call_id: str
    name: str
    arguments: dict
    result: str


# ============================================================
# Agent クラス
# ============================================================

class Agent:
    """
    ストリーミングベースのエージェント。

    stream_run(user_message) でジェネレータを返す。
    UIはイベントを消費してリアルタイム表示する。

    turn_done は必ず最後に yield される（エラー・上限到達時も同様）。
    """

    def __init__(
        self,
        client: OpenRouterClient,
        work_dir: str = ".",
        max_iterations: int = 20,
    ) -> None:
        self.client = client
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.state = AgentState()

    def reset_conversation(self) -> None:
        """会話履歴をリセットする。"""
        self.state = AgentState()

    def load_conversation(self, conversation: list[dict]) -> None:
        """保存済み会話履歴を復元する。"""
        self.state.conversation = list(conversation)

    def stream_run(
        self, user_message: str
    ) -> Generator[tuple[str, Any], None, None]:
        """
        エージェントループを実行するジェネレータ。

        turn_done は必ず最後に yield される。
        """
        self.state.conversation.append({"role": "user", "content": user_message})
        start_time = time.time()

        try:
            yield from self._agent_loop(start_time)
        finally:
            # 例外・中断があっても必ず turn_done を発行
            yield ("turn_done", time.time() - start_time)

    def _agent_loop(
        self, start_time: float
    ) -> Generator[tuple[str, Any], None, None]:
        """エージェントループ本体（turn_done の yield は stream_run が担う）。"""
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1
            self.state.iteration_count += 1
            yield ("api_start", iteration)

            logger.debug("エージェントループ: iteration=%d", iteration)

            # ── ストリーミングAPIを呼び出す ──────────────────────────────
            text_content = ""
            pending_tool_calls: list[dict] = []
            had_error = False

            for ev in self.client.stream_events(
                messages=self._get_messages_for_api(),
                tools=TOOL_DEFINITIONS,
                system_prompt=SYSTEM_PROMPT,
            ):
                match ev.type:
                    case "text_delta":
                        text_content += ev.data
                        yield ("text_delta", ev.data)

                    case "text_done":
                        # text_done の data を正とする（main側のバッファと同期）
                        yield ("text_done", ev.data)

                    case "tool_call_ready":
                        pending_tool_calls.append(ev.data)

                    case "error":
                        logger.error("API エラー: %s", ev.data)
                        yield ("error", ev.data)
                        had_error = True

                    case "stream_done":
                        logger.debug(
                            "ストリーム完了: text=%d chars, tools=%d",
                            len(text_content),
                            len(pending_tool_calls),
                        )

            # ── アシスタントメッセージを履歴に追加 ─────────────────────
            # エラーがあっても、受信済みのテキスト／ツール情報は保持する
            if text_content or pending_tool_calls:
                assistant_msg: dict = {"role": "assistant"}
                assistant_msg["content"] = text_content or None

                if pending_tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in pending_tool_calls
                    ]
                self.state.conversation.append(assistant_msg)

            if had_error:
                logger.debug("エラーによりループを終了")
                return

            # ── ツール呼び出しがなければ終了 ─────────────────────────────
            if not pending_tool_calls:
                logger.debug("ツールなし → ループ終了")
                return

            # ── ツールを順番に実行 ────────────────────────────────────────
            for tc in pending_tool_calls:
                yield ("tool_call_start", tc)

                try:
                    arguments = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    arguments = {}
                    logger.warning("ツール引数のJSONパース失敗: %r", tc["arguments"])

                logger.debug("ツール実行: %s(%s)", tc["name"], list(arguments.keys()))
                result_str = execute_tool(
                    tc["name"], arguments, work_dir=self.work_dir
                )
                self.state.tool_call_count += 1

                result = ToolCallResult(
                    tool_call_id=tc["id"],
                    name=tc["name"],
                    arguments=arguments,
                    result=result_str,
                )
                yield ("tool_result", result)

                self.state.conversation.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })

        # while ループ正常終了 = max_iterations 到達
        yield ("max_iterations", self.max_iterations)

    def _get_messages_for_api(self) -> list[dict]:
        """
        API送信用メッセージリストを返す。
        会話が長すぎる場合は古いメッセージを削除する。
        """
        conv = self.state.conversation
        if len(conv) <= MAX_CONVERSATION_MESSAGES:
            return conv

        first = conv[:1]
        recent = conv[-KEEP_RECENT_MESSAGES:]
        trimmed = first + recent
        logger.info("会話を削減: %d → %d メッセージ", len(conv), len(trimmed))
        return trimmed

    @property
    def conversation(self) -> list[dict]:
        """現在の会話履歴（セッション保存用）。"""
        return self.state.conversation

    @property
    def stats(self) -> dict:
        return {
            "iteration_count": self.state.iteration_count,
            "tool_call_count": self.state.tool_call_count,
            "conversation_length": len(self.state.conversation),
        }
