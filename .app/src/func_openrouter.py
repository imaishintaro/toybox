"""
チャット API クライアントモジュール。

OpenRouter と Azure OpenAI の両方に対応。
ストリーミングロジックは _BaseChatClient に集約し、
OpenRouterClient / AzureOpenAIClient がそれを継承する。
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Generator

import httpx
from openai import OpenAI, AzureOpenAI

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"
LMSTUDIO_BASE_URL = "http://localhost:1234/v1"

# 接続リトライ設定
MAX_CONNECT_RETRIES = 3
RETRY_WAIT_BASE = 1.5

# タイムアウト設定
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 60.0
WRITE_TIMEOUT = 15.0


@dataclass
class StreamEvent:
    """ストリームから生成されるイベント。"""

    type: str
    # "text_delta"      : str   テキストチャンク（逐次）
    # "text_done"       : str   テキスト全体（ストリーム完了後）
    # "tool_call_ready" : dict  {id, name, arguments}  ツール呼び出し1件
    # "stream_done"     : None  正常終了
    # "error"           : str   エラーメッセージ
    data: Any


def _make_httpx_client() -> httpx.Client:
    """タイムアウト設定済みの httpx.Client を生成する。"""
    return httpx.Client(
        timeout=httpx.Timeout(
            connect=CONNECT_TIMEOUT,
            read=READ_TIMEOUT,
            write=WRITE_TIMEOUT,
            pool=CONNECT_TIMEOUT,
        )
    )


class _BaseChatClient:
    """
    OpenRouter / Azure OpenAI 共通のストリーミングロジック。

    サブクラスは self.model と self._client を設定すること。
    """

    model: str
    _client: Any  # openai.OpenAI または openai.AzureOpenAI

    def stream_events(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> Generator[StreamEvent, None, None]:
        """
        ストリーミング API を呼び出し、構造化イベントを yield するジェネレータ。

        接続フェーズのみリトライ。ストリーミング中はリトライしない。
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
        last_error: Exception | None = None

        for attempt in range(1, MAX_CONNECT_RETRIES + 1):
            try:
                logger.debug("API接続試行 %d/%d", attempt, MAX_CONNECT_RETRIES)
                stream = self._client.chat.completions.create(**kwargs)
                logger.debug("API接続成功")
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    "接続失敗 (%d/%d): %s: %s", attempt, MAX_CONNECT_RETRIES, type(e).__name__, e
                )
                if attempt < MAX_CONNECT_RETRIES:
                    time.sleep(RETRY_WAIT_BASE * attempt)

        if stream is None:
            msg = (
                f"接続失敗（{MAX_CONNECT_RETRIES}回試行）: "
                f"{type(last_error).__name__}: {last_error}"
            )
            logger.error(msg)
            yield StreamEvent("error", msg)
            return

        # ── フェーズ2: ストリーミング受信（リトライなし） ─────────────────
        text_buffer = ""
        tool_calls_acc: dict[int, dict] = {}
        chunk_count = 0
        finish_reason: str | None = None

        try:
            for chunk in stream:
                chunk_count += 1
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # finish_reason は通常ストリームの最終チャンクに設定される
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                if delta.content:
                    text_buffer += delta.content
                    yield StreamEvent("text_delta", delta.content)

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
                chunk_count, len(text_buffer), len(tool_calls_acc),
            )

        except httpx.ReadTimeout:
            msg = (
                f"ストリームタイムアウト（{READ_TIMEOUT:.0f}秒間応答なし）。"
                " 再度お試しください。"
            )
            logger.error(msg)
            if text_buffer:
                yield StreamEvent("text_done", text_buffer)
            yield StreamEvent("error", msg)
            return

        except Exception as e:
            msg = f"ストリームエラー（{chunk_count}チャンク受信後）: {type(e).__name__}: {e}"
            logger.error(msg)
            if text_buffer:
                yield StreamEvent("text_done", text_buffer)
            yield StreamEvent("error", msg)
            return

        # ── フェーズ3: 完成イベントを一括通知 ────────────────────────────
        if text_buffer:
            yield StreamEvent("text_done", text_buffer)

        # max_tokens に達して応答が途中で打ち切られた場合に通知
        if finish_reason == "length":
            logger.warning("応答が max_tokens に達して打ち切られました")
            yield StreamEvent("truncated", None)

        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            tc["name"] = tc["name"].strip()  # モデルが先頭/末尾スペースを送ることがある
            yield StreamEvent("tool_call_ready", tc)

        yield StreamEvent("stream_done", None)

    def chat_completion(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        """
        非ストリーミングで API を呼び出してテキストを返す。

        JSON レスポンスが必要な場面（オーケストレーターなど）で使用。
        """
        full_messages = _build_messages(messages, system_prompt)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        content = response.choices[0].message.content or ""
        logger.debug("chat_completion 完了: %d 文字", len(content))
        return content


class OpenRouterClient(_BaseChatClient):
    """OpenRouter API クライアント。"""

    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            http_client=_make_httpx_client(),
            default_headers={
                "HTTP-Referer": "https://github.com/claw-agent",
                "X-Title": "claw - Claude-Like Agent Workflow",
            },
        )


class AzureOpenAIClient(_BaseChatClient):
    """Azure OpenAI API クライアント。"""

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        deployment: str,
        api_version: str,
    ) -> None:
        self.model = deployment  # Azure ではデプロイ名がモデル名
        self._client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
            http_client=_make_httpx_client(),
        )


class LocalLLMClient(_BaseChatClient):
    """
    Ollama / LM Studio など OpenAI 互換のローカル LLM クライアント。

    ツール呼び出し（function calling）に対応したモデルが必要。
    対応モデル例:
      Ollama  : llama3.3, qwen2.5-coder, mistral-nemo など
      LMStudio: 同上（モデル次第）
    """

    def __init__(self, model: str, base_url: str = OLLAMA_BASE_URL) -> None:
        self.model = model
        self._client = OpenAI(
            api_key="local",      # 認証不要だが空文字は SDK が拒否するため任意の文字列を設定
            base_url=base_url,
            http_client=_make_httpx_client(),
        )


def create_chat_client(config: dict) -> _BaseChatClient:
    """
    設定に基づいてチャットクライアントを生成するファクトリ。

    provider の値に応じて返すクライアントが変わる:
      "azure"    → AzureOpenAIClient
      "ollama"   → LocalLLMClient (localhost:11434)
      "lmstudio" → LocalLLMClient (localhost:1234)
      その他     → OpenRouterClient (デフォルト)
    """
    provider = config.get("provider", "openrouter")
    if provider == "azure":
        return AzureOpenAIClient(
            api_key=config["api_key"],
            endpoint=config["azure_endpoint"],
            deployment=config["azure_deployment"],
            api_version=config["azure_api_version"],
        )
    if provider in ("ollama", "lmstudio"):
        return LocalLLMClient(
            model=config["model"],
            base_url=config["local_base_url"],
        )
    return OpenRouterClient(api_key=config["api_key"], model=config["model"])


def _build_messages(messages: list[dict], system_prompt: str | None) -> list[dict]:
    """システムプロンプトをメッセージ先頭に挿入する。"""
    if not system_prompt:
        return messages
    if messages and messages[0].get("role") == "system":
        return messages
    return [{"role": "system", "content": system_prompt}] + messages
