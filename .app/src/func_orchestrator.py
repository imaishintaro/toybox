"""
オーケストレーターモジュール。

プロジェクト開始時に仕様を分析し、
エージェントの役割・タスクを割り当てる計画を作成する。

plan_project() が OpenRouter API を呼び出して JSON 計画を生成し、
失敗時はデフォルト2エージェント計画にフォールバックする。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ============================================================
# データクラス
# ============================================================

@dataclass
class AgentAssignment:
    """1エージェントへの割り当て情報。"""

    agent_name: str              # 例: "claw_1"
    role: str                    # 例: "バックエンド開発者"
    task: str                    # 具体的なタスク説明
    depends_on: list[str] = field(default_factory=list)  # 依存エージェント名


@dataclass
class ProjectPlan:
    """プロジェクト全体の実行計画。"""

    project_description: str
    agents: list[AgentAssignment]
    execution_order: Literal["sequential", "parallel"]
    board_init: str              # 共有ボードの初期内容


# ============================================================
# オーケストレーター API 呼び出し
# ============================================================

_ORCHESTRATOR_SYSTEM = """You are a software project orchestrator.
Analyze the given project description and create an agent assignment plan.

Rules:
- Assign at least 2 agents (more if the project requires it)
- Agent names must be: claw_1, claw_2, claw_3, ... (sequential numbers)
- Each agent should have a clear, specific role and task
- Prefer sequential execution unless tasks are truly independent
- Response MUST be valid JSON only — no markdown, no explanation

JSON schema:
{
  "agents": [
    {
      "agent_name": "claw_1",
      "role": "Agent role title",
      "task": "Detailed task description",
      "depends_on": []
    }
  ],
  "execution_order": "sequential",
  "board_init": "# Project: <title>\\n\\n## Overview\\n<overview>\\n\\n## Progress\\n"
}
"""

_ORCHESTRATOR_USER_TMPL = """Project description:
{description}

Working directory: {work_dir}

Create a multi-agent plan for this project. Respond with JSON only."""


def plan_project(
    client,
    project_description: str,
    work_dir: str,
    claw_prefix: str = "claw",
) -> ProjectPlan:
    """
    OpenRouter API を呼び出してプロジェクト計画を生成する。

    Args:
        client: OpenRouterClient インスタンス
        project_description: プロジェクトの説明
        work_dir: 作業ディレクトリ
        claw_prefix: エージェント名のプレフィクス（例: "claw" → "claw_1", "claw_2"）

    Returns:
        ProjectPlan（失敗時はデフォルト2エージェント計画）
    """
    user_msg = _ORCHESTRATOR_USER_TMPL.format(
        description=project_description,
        work_dir=work_dir,
    )

    try:
        logger.info("オーケストレーター: プロジェクト計画を生成中...")
        response_text = client.chat_completion(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=_ORCHESTRATOR_SYSTEM,
            temperature=0.3,
            max_tokens=2000,
        )
        plan = _parse_plan(response_text, project_description, claw_prefix)
        logger.info(
            "オーケストレーター: 計画生成完了 (%d エージェント)", len(plan.agents)
        )
        return plan

    except Exception as e:
        logger.warning("オーケストレーター: API 呼び出し失敗、デフォルト計画を使用: %s", e)
        return _default_plan(project_description, claw_prefix)


def _parse_plan(
    response_text: str,
    project_description: str,
    claw_prefix: str,
) -> ProjectPlan:
    """
    API レスポンスの JSON をパースして ProjectPlan を生成する。

    パース失敗や不正データの場合はデフォルト計画を返す。
    """
    # JSON ブロック抽出（```json ... ``` などをトリム）
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("JSON パース失敗: %s\n応答: %r", e, response_text[:300])
        return _default_plan(project_description, claw_prefix)

    agents_raw = data.get("agents", [])
    if not isinstance(agents_raw, list) or len(agents_raw) < 2:
        logger.warning("エージェント数が不足 (%d 件)、デフォルト計画を使用", len(agents_raw))
        return _default_plan(project_description, claw_prefix)

    agents: list[AgentAssignment] = []
    for i, a in enumerate(agents_raw):
        if not isinstance(a, dict):
            continue
        # エージェント名を claw_prefix で正規化
        name = a.get("agent_name", f"{claw_prefix}_{i + 1}")
        if not name.startswith(claw_prefix):
            name = f"{claw_prefix}_{i + 1}"
        agents.append(
            AgentAssignment(
                agent_name=name,
                role=a.get("role", f"Agent {i + 1}"),
                task=a.get("task", "タスク未定義"),
                depends_on=a.get("depends_on", []),
            )
        )

    if len(agents) < 2:
        return _default_plan(project_description, claw_prefix)

    execution_order = data.get("execution_order", "sequential")
    if execution_order not in ("sequential", "parallel"):
        execution_order = "sequential"

    board_init = data.get(
        "board_init",
        f"# プロジェクト\n\n{project_description}\n\n## 進捗\n\n",
    )

    return ProjectPlan(
        project_description=project_description,
        agents=agents,
        execution_order=execution_order,
        board_init=board_init,
    )


def _default_plan(project_description: str, claw_prefix: str) -> ProjectPlan:
    """パース失敗時のデフォルト2エージェント計画。"""
    return ProjectPlan(
        project_description=project_description,
        agents=[
            AgentAssignment(
                agent_name=f"{claw_prefix}_1",
                role="設計・実装担当",
                task=(
                    f"以下のプロジェクトを設計・実装してください:\n{project_description}\n\n"
                    "まず要件を整理し、ファイル構成を決定してから実装を進めてください。"
                    "完了したら共有ボードに結果を記録してください。"
                ),
                depends_on=[],
            ),
            AgentAssignment(
                agent_name=f"{claw_prefix}_2",
                role="レビュー・テスト担当",
                task=(
                    f"以下のプロジェクトのコードをレビュー・テストしてください:\n{project_description}\n\n"
                    "共有ボードで前のエージェントの作業結果を確認し、"
                    "コードの品質チェック・テスト実行・改善提案を行ってください。"
                ),
                depends_on=[f"{claw_prefix}_1"],
            ),
        ],
        execution_order="sequential",
        board_init=(
            f"# プロジェクト\n\n{project_description}\n\n"
            "## エージェント作業ログ\n\n"
        ),
    )


def format_plan_display(plan: ProjectPlan) -> str:
    """計画をユーザー確認用の文字列にフォーマットする。"""
    lines = [
        f"プロジェクト: {plan.project_description}",
        f"実行順序: {'逐次' if plan.execution_order == 'sequential' else '並列'}",
        "",
        "エージェント割り当て:",
    ]
    for i, agent in enumerate(plan.agents, 1):
        deps = (
            f"  (依存: {', '.join(agent.depends_on)})"
            if agent.depends_on
            else ""
        )
        lines.append(f"  {i}. [{agent.agent_name}] {agent.role}{deps}")
        # タスクを最大100文字に短縮
        task_preview = agent.task[:100] + "..." if len(agent.task) > 100 else agent.task
        lines.append(f"     → {task_preview}")
    return "\n".join(lines)
