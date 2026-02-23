"""
OpenRouter APIクライアントモジュール。

修正済みバグ:
  - タイムアウト未設定 → httpx.Timeout を設定
  - リトライが途中yield後にバッファリセット → 接続フェーズのみリトライ
  - エラー後 return なし → 明示的 return を追加
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Generator

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# 接続リトライ設定（ストリーミング開始前のみ有効）
MAX_CONNECT_RETRIES = 3
RETRY_WAIT_BASE = 1.5  # 秒（指数バックオフの基数）

# タイムアウト設定
CONNECT_TIMEOUT = 15.0   # 接続確立
READ_TIMEOUT = 60.0      # チャンク間の最大待機（これを超えると hung と判定）
WRITE_TIMEOUT = 15.0


@dataclass
class StreamEvent:
    """ストリームから生成されるイベント。"""

    type: str
    # "text_delta"      : str  テキストチャンク（逐次）
    # "text_done"       : str  テキスト全体（ストリーム完了後）
    # "tool_call_ready" : dict {id, name, arguments}  ツール呼び出し1件
    # "stream_done"     : None  正常終了
    # "error"           : str  エラーメッセージ
    data: Any


class OpenRouterClient:
    """OpenRouter APIクライアント（ストリーミング対応）。"""

    def __init__(self, api_key: str, model: str) -> None:
        """
        クライアントを初期化する。

        Args:
            api_key: OpenRouter APIキー
            model: 使用するモデル (例: anthropic/claude-3.5-sonnet)
        """
        self.model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            # httpx タイムアウトを明示設定（ハング防止の要）
            http_client=httpx.Client(
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT,
                    read=READ_TIMEOUT,
                    write=WRITE_TIMEOUT,
                    pool=CONNECT_TIMEOUT,
                )
            ),
            default_headers={
                "HTTP-Referer": "https://github.com/claw-agent",
                "X-Title": "claw - Claude-Like Agent Workflow",
            },
        )

    def stream_events(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> Generator[StreamEvent, None, None]:
        """
        ストリーミングAPIを呼び出し、構造化イベントを生成するジェネレータ。

        設計方針:
          - 接続フェーズ（stream オブジェクト取得まで）: MAX_CONNECT_RETRIES 回リトライ
          - ストリーミングフェーズ: リトライしない（yield済みイベントと整合が取れないため）
          - タイムアウト: httpx.Client で READ_TIMEOUT 秒ごとのチャンク到着を保証

        Yields:
            StreamEvent (type, data) タプルラッパー
        """
        full_messages = _build_messages(messages, system_prompt)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # ── フェーズ1: 接続（リトライあり） ──────────────────────────────
        stream = None
        last_connect_error: Exception | None = None

        for attempt in range(1, MAX_CONNECT_RETRIES + 1):
            try:
                logger.debug("API接続試行 %d/%d", attempt, MAX_CONNECT_RETRIES)
                stream = self._client.chat.completions.create(**kwargs)
                logger.debug("API接続成功")
                break
            except Exception as e:
                last_connect_error = e
                logger.warning(
                    "接続失敗 (%d/%d): %s: %s",
                    attempt,
                    MAX_CONNECT_RETRIES,
                    type(e).__name__,
                    e,
                )
                if attempt < MAX_CONNECT_RETRIES:
                    wait = RETRY_WAIT_BASE * attempt
                    logger.debug("%.1f秒後にリトライします", wait)
                    time.sleep(wait)

        if stream is None:
            msg = f"接続失敗（{MAX_CONNECT_RETRIES}回試行）: {type(last_connect_error).__name__}: {last_connect_error}"
            logger.error(msg)
            yield StreamEvent("error", msg)
            return  # ← 必須: ジェネレータを明示的に終了

        # ── フェーズ2: ストリーミング受信（リトライなし） ─────────────────
        text_buffer = ""
        tool_calls_acc: dict[int, dict] = {}   # index → {id, name, arguments}
        chunk_count = 0

        try:
            for chunk in stream:
                chunk_count += 1

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # テキストチャンク
                if delta.content:
                    text_buffer += delta.content
                    yield StreamEvent("text_delta", delta.content)

                # ツール呼び出しデルタを蓄積
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc_delta.id:
                            tool_calls_acc[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_acc[idx]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments

            logger.debug(
                "ストリーム完了: chunks=%d  text=%d chars  tools=%d",
                chunk_count,
                len(text_buffer),
                len(tool_calls_acc),
            )

        except httpx.ReadTimeout:
            # READ_TIMEOUT 秒間チャンクが来なかった場合
            msg = (
                f"ストリームタイムアウト（{READ_TIMEOUT:.0f}秒間応答なし）。"
                " APIが混雑している可能性があります。再度お試しください。"
            )
            logger.error(msg)
            # 受信済みテキストがあれば先に通知
            if text_buffer:
                yield StreamEvent("text_done", text_buffer)
            yield StreamEvent("error", msg)
            return  # ← 明示的終了

        except Exception as e:
            msg = f"ストリームエラー（{chunk_count}チャンク受信後）: {type(e).__name__}: {e}"
            logger.error(msg)
            if text_buffer:
                yield StreamEvent("text_done", text_buffer)
            yield StreamEvent("error", msg)
            return  # ← 明示的終了

        # ── フェーズ3: 完成イベントを一括通知 ────────────────────────────
        if text_buffer:
            yield StreamEvent("text_done", text_buffer)

        for idx in sorted(tool_calls_acc.keys()):
            yield StreamEvent("tool_call_ready", tool_calls_acc[idx])

        yield StreamEvent("stream_done", None)


def _build_messages(messages: list[dict], system_prompt: str | None) -> list[dict]:
    """システムプロンプトをメッセージ先頭に挿入する。"""
    if not system_prompt:
        return messages
    if messages and messages[0].get("role") == "system":
        return messages
    return [{"role": "system", "content": system_prompt}] + messages
