"""
埋め込み API クライアントとコンテキスト圧縮モジュール。

EMBEDDING_MODEL が設定されていない場合は圧縮を行わない（create_embedding_client が None を返す）。

圧縮アルゴリズム:
  1. 会話をターン（ユーザーメッセージを起点としたグループ）に分割
  2. 末尾 KEEP_RECENT_TURNS ターンは常に保持
  3. 中間ターンの埋め込みを取得し、コサイン類似度が閾値以上のペアを検出
  4. 冗長ターン（後発のもの）を削除
"""
import logging
import math
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 冗長とみなすコサイン類似度の閾値（0〜1、高いほど厳しい）
SIMILARITY_THRESHOLD = 0.92

# 末尾から必ず保持するターン数
KEEP_RECENT_TURNS = 4

# 圧縮を発動するメッセージ数の閾値
COMPRESS_THRESHOLD = 50


class EmbeddingClient:
    """埋め込み API クライアント（OpenRouter / Azure OpenAI 共通）。"""

    def __init__(self, openai_client: Any, model: str) -> None:
        self._client = openai_client
        self.model = model

    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        テキストリストの埋め込みベクトルを取得する。

        Args:
            texts: 埋め込みを取得するテキストのリスト

        Returns:
            各テキストの埋め込みベクトルのリスト（入力と同じ順序）
        """
        # 空文字列はスペースに置換（一部 API が空文字を拒否するため）
        safe_texts = [t if t.strip() else " " for t in texts]
        response = self._client.embeddings.create(
            model=self.model,
            input=safe_texts,
        )
        # data はインデックス順を保証しないのでソート
        sorted_data = sorted(response.data, key=lambda e: e.index)
        return [e.embedding for e in sorted_data]


def create_embedding_client(config: dict) -> "EmbeddingClient | None":
    """
    設定に基づいて埋め込みクライアントを生成するファクトリ。

    EMBEDDING_MODEL が設定されていない場合は None を返す（圧縮無効）。
    """
    embedding_model = config.get("embedding_model", "").strip()
    if not embedding_model:
        logger.debug("EMBEDDING_MODEL 未設定: コンテキスト圧縮を無効化")
        return None

    http_client = httpx.Client(
        timeout=httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0)
    )

    provider = config.get("embedding_provider", "openrouter").strip().lower()

    if provider == "azure":
        from openai import AzureOpenAI
        openai_client = AzureOpenAI(
            api_key=config["embedding_api_key"],
            azure_endpoint=config["embedding_azure_endpoint"],
            api_version=config.get("embedding_azure_api_version", "2024-08-01-preview"),
            http_client=http_client,
        )
    else:
        from openai import OpenAI
        openai_client = OpenAI(
            api_key=config["embedding_api_key"],
            base_url="https://openrouter.ai/api/v1",
            http_client=http_client,
        )

    logger.info(
        "埋め込みクライアント作成: provider=%s model=%s", provider, embedding_model
    )
    return EmbeddingClient(openai_client, embedding_model)


def compress_conversation(
    conversation: list[dict],
    embedding_client: EmbeddingClient,
    keep_recent_turns: int = KEEP_RECENT_TURNS,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> list[dict]:
    """
    埋め込みを使って会話の冗長なターンを削除し圧縮する。

    会話は「ターン」（ユーザーメッセージを起点としたメッセージグループ）
    に分割して処理するため、ツール呼び出し結果などの整合性を保つ。

    Args:
        conversation: 会話メッセージリスト
        embedding_client: 埋め込みクライアント
        keep_recent_turns: 末尾から常に保持するターン数
        similarity_threshold: 冗長とみなすコサイン類似度の閾値

    Returns:
        圧縮後の会話メッセージリスト（圧縮不要 or 失敗時は元のリストを返す）
    """
    turns = _group_into_turns(conversation)

    # 先頭 + 末尾だけ → 圧縮する中間がない
    if len(turns) <= keep_recent_turns + 1:
        return conversation

    first_turns = turns[:1]                       # 最初のターン（文脈の起点）
    recent_turns = turns[-keep_recent_turns:]      # 末尾ターン（必ず保持）
    compress_candidates = turns[1:-keep_recent_turns]

    if not compress_candidates:
        return conversation

    # 埋め込みを取得
    try:
        texts = [_turn_to_text(t) for t in compress_candidates]
        embeddings = embedding_client.get_embeddings(texts)
    except Exception as e:
        logger.warning("埋め込み取得失敗、圧縮をスキップ: %s", e)
        return conversation

    # 類似ターンを検出（後発のものを削除）
    keep = [True] * len(compress_candidates)
    for i in range(len(compress_candidates)):
        if not keep[i]:
            continue
        for j in range(i + 1, len(compress_candidates)):
            if not keep[j]:
                continue
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim >= similarity_threshold:
                keep[j] = False

    removed = keep.count(False)
    if removed > 0:
        logger.info(
            "コンテキスト圧縮: %d / %d ターンを削除（類似度 >= %.2f）",
            removed, len(compress_candidates), similarity_threshold,
        )

    compressed_turns = [t for t, k in zip(compress_candidates, keep) if k]

    result: list[dict] = []
    for turn in first_turns + compressed_turns + recent_turns:
        result.extend(turn)
    return result


def _group_into_turns(conversation: list[dict]) -> list[list[dict]]:
    """
    会話をターン（ユーザーメッセージを起点としたグループ）に分割する。

    ターン = 1つのユーザーメッセージ + それに続くアシスタント・ツールメッセージ群
    """
    turns: list[list[dict]] = []
    current: list[dict] = []

    for msg in conversation:
        if msg.get("role") == "user" and current:
            turns.append(current)
            current = [msg]
        else:
            current.append(msg)

    if current:
        turns.append(current)

    return turns


def _turn_to_text(turn: list[dict]) -> str:
    """ターンを埋め込み用テキスト表現に変換する（400文字/メッセージで打ち切り）。"""
    parts = []
    for msg in turn:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        # マルチパートコンテンツ（画像付きなど）はテキスト部分のみ抽出
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )

        if content:
            parts.append(f"{role}: {content[:400]}")

    return "\n".join(parts)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """コサイン類似度を計算する（0〜1）。"""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)
