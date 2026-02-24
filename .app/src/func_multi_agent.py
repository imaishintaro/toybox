"""
マルチエージェント実行モジュール。

MultiAgentRunner がオーケストレーター計画に従って複数エージェントを実行する。
各エージェントの実行はログファイルに記録され、tmux ウィンドウで監視できる。

実行モード:
  - sequential: エージェントを順番に実行（前のエージェントの完了を待つ）
  - parallel:   スレッドで並行実行（注: 多くの場合 sequential が安全）
"""
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from func_agent import Agent, ToolCallResult
from func_orchestrator import AgentAssignment, ProjectPlan
from func_openrouter import OpenRouterClient

logger = logging.getLogger(__name__)


# ============================================================
# エージェントロガー
# ============================================================

class AgentLogger:
    """
    エージェントのイベントをログファイルに JSON Lines 形式で書き込む。

    tmux の watch.py がこのファイルを監視して表示する。
    """

    def __init__(self, log_path: str, agent_name: str) -> None:
        self.log_path = log_path
        self.agent_name = agent_name
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

        # 起動ヘッダーを書き込む
        self._write({
            "type": "start",
            "agent": agent_name,
            "message": f"エージェント {agent_name} を起動しました",
        })

    def log(self, event_type: str, message: str, extra: dict | None = None) -> None:
        """イベントをログファイルに追記する。"""
        entry: dict[str, Any] = {
            "type": event_type,
            "agent": self.agent_name,
            "message": message,
        }
        if extra:
            entry.update(extra)
        self._write(entry)

    def _write(self, entry: dict) -> None:
        """JSON Lines 形式でログファイルに書き込む。"""
        entry["timestamp"] = datetime.now().strftime("%H:%M:%S")
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("ログ書き込み失敗: %s: %s", self.log_path, e)


# ============================================================
# エージェント実行関数
# ============================================================

def _run_single_agent(
    assignment: AgentAssignment,
    client: OpenRouterClient,
    work_dir: str,
    board_path: str,
    log_dir: str,
    max_iterations: int,
    result_queue: "queue.Queue | None" = None,
    event_callback=None,
) -> dict:
    """
    1エージェントを実行し、結果を返す。

    Args:
        assignment: エージェントの割り当て情報
        client: OpenRouterClient
        work_dir: 作業ディレクトリ
        board_path: 共有ボードファイルのパス
        log_dir: ログファイルのディレクトリ
        max_iterations: 最大反復回数
        result_queue: 並列実行時の結果キュー
        event_callback: イベント発生時のコールバック関数 (event_type, agent_name, data)

    Returns:
        実行結果 dict
    """
    agent_name = assignment.agent_name
    log_path = str(Path(log_dir) / f"{agent_name}.log")
    ag_logger = AgentLogger(log_path, agent_name)

    # ロールシステムプロンプトを生成
    board_read_instruction = (
        f"\n## 開始前に必ず行うこと\n"
        f"read_file ツールで共有ボード ({board_path}) を読み、"
        f"前のエージェントの成果を確認してから作業を開始してください。\n"
    ) if assignment.depends_on else ""

    role_prompt = (
        f"## あなたのエージェント名: {agent_name}\n"
        f"## あなたの役割: {assignment.role}\n"
        f"{board_read_instruction}\n"
        f"## あなたのタスク\n{assignment.task}\n\n"
        f"## 絶対ルール（違反禁止）\n"
        f"- **テキストだけで回答して終了することは禁止**\n"
        f"- **必ず write_file / edit_file / bash のいずれかを使って成果物を残すこと**\n"
        f"- タスクに「ファイルを作成」と書いてある場合、実際に write_file で作成すること\n"
        f"- 「確認した」「設計した」だけで終わらず、必ずファイルに書き出すこと\n"
        f"- タスク完了時は必ず post_to_board ツールで成果を共有ボードに投稿すること\n"
    )

    agent = Agent(
        client=client,
        work_dir=work_dir,
        max_iterations=max_iterations,
        agent_name=agent_name,
        role_system_prompt=role_prompt,
        board_path=board_path,
    )

    ag_logger.log("task", f"タスク開始: {assignment.role}", {"task": assignment.task[:200]})

    if event_callback:
        event_callback("agent_start", agent_name, assignment)

    start_time = time.time()
    success = True
    error_msg = None

    try:
        for event_type, data in agent.stream_run(assignment.task):
            match event_type:
                case "api_start":
                    ag_logger.log("iteration", f"イテレーション {data} 開始")
                    if event_callback:
                        event_callback("api_start", agent_name, data)

                case "text_delta":
                    pass  # デルタは logging しない（ログが膨大になるため）

                case "text_done":
                    if data:
                        ag_logger.log("text", data[:500] + ("..." if len(data) > 500 else ""))
                        if event_callback:
                            event_callback("text_done", agent_name, data)

                case "tool_call_start":
                    tc = data
                    ag_logger.log(
                        "tool_call",
                        f"ツール呼び出し: {tc['name']}",
                        {"tool": tc["name"]},
                    )
                    if event_callback:
                        event_callback("tool_call_start", agent_name, tc)

                case "tool_result":
                    result: ToolCallResult = data
                    is_error = result.result.startswith("[ERROR]")
                    ag_logger.log(
                        "tool_result",
                        f"{result.name}: {'エラー' if is_error else '成功'}",
                        {"tool": result.name, "error": is_error},
                    )
                    if event_callback:
                        event_callback("tool_result", agent_name, result)

                case "error":
                    ag_logger.log("level", f"エラー: {data}", {"level": "ERROR"})
                    success = False
                    error_msg = data
                    if event_callback:
                        event_callback("error", agent_name, data)

                case "max_iterations":
                    ag_logger.log("level", f"最大反復回数 ({data}) に達しました", {"level": "WARNING"})
                    if event_callback:
                        event_callback("max_iterations", agent_name, data)

                case "turn_done":
                    elapsed = data
                    ag_logger.log(
                        "done",
                        f"タスク完了: {elapsed:.1f}秒",
                        {
                            "elapsed": elapsed,
                            "iterations": agent.stats["iteration_count"],
                            "tools": agent.stats["tool_call_count"],
                        },
                    )
                    if event_callback:
                        event_callback("turn_done", agent_name, data)

    except Exception as e:
        success = False
        error_msg = str(e)
        ag_logger.log("level", f"予期しないエラー: {e}", {"level": "ERROR"})
        logger.exception("エージェント %s で予期しないエラー", agent_name)

    elapsed_total = time.time() - start_time
    result_data = {
        "agent_name": agent_name,
        "role": assignment.role,
        "success": success,
        "error": error_msg,
        "elapsed": elapsed_total,
        "stats": agent.stats,
    }

    if result_queue is not None:
        result_queue.put(result_data)

    return result_data


# ============================================================
# MultiAgentRunner
# ============================================================

class MultiAgentRunner:
    """
    ProjectPlan に従って複数エージェントを実行するランナー。

    sequential モード: エージェントを順番に実行
    parallel モード: スレッドで並行実行（depends_on を考慮）
    """

    def __init__(
        self,
        client: OpenRouterClient,
        work_dir: str,
        max_iterations: int = 20,
        sessions_dir: str = ".app/sessions",
    ) -> None:
        self.client = client
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.sessions_dir = sessions_dir

    def prepare_run(self, plan: ProjectPlan) -> tuple[str, str]:
        """
        実行前にディレクトリを作成して共有ボードを初期化する。

        tmux セットアップより前に呼び出すことで、
        ウォッチャーが正しいファイルパスを監視できる。

        Returns:
            (log_dir, board_path) のタプル
        """
        log_dir, board_path = self._setup_directories(plan)
        self._init_board(board_path, plan.board_init)
        logger.info("実行ディレクトリ作成: log_dir=%s, board=%s", log_dir, board_path)
        return log_dir, board_path

    def execute_run(
        self,
        plan: ProjectPlan,
        log_dir: str,
        board_path: str,
        event_callback=None,
    ) -> dict[str, dict]:
        """
        prepare_run() で作成したディレクトリでエージェントを実行する。

        Args:
            plan: ProjectPlan オブジェクト
            log_dir: prepare_run() が返したログディレクトリ
            board_path: prepare_run() が返した共有ボードパス
            event_callback: イベントコールバック (event_type, agent_name, data)

        Returns:
            {agent_name: result_dict} の辞書
        """
        logger.info(
            "マルチエージェント実行開始: %d エージェント (%s)",
            len(plan.agents),
            plan.execution_order,
        )

        if plan.execution_order == "parallel":
            results = self._run_parallel(plan, log_dir, board_path, event_callback)
        else:
            results = self._run_sequential(plan, log_dir, board_path, event_callback)

        self._write_summary(board_path, results)
        return results

    def run(
        self,
        plan: ProjectPlan,
        event_callback=None,
    ) -> dict[str, dict]:
        """
        計画に従ってエージェントを実行する（後方互換メソッド）。

        tmux 連携が不要な場合は直接このメソッドを呼び出せる。
        tmux ウォッチャーと連携する場合は prepare_run() → tmux setup → execute_run() の順で呼ぶこと。

        Args:
            plan: ProjectPlan オブジェクト
            event_callback: イベントコールバック (event_type, agent_name, data)

        Returns:
            {agent_name: result_dict} の辞書
        """
        log_dir, board_path = self.prepare_run(plan)
        return self.execute_run(plan, log_dir, board_path, event_callback)

    def _setup_directories(self, plan: ProjectPlan) -> tuple[str, str]:
        """ログ・ボードのディレクトリを準備する。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = Path(self.sessions_dir) / f"project_{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)

        log_dir = str(run_dir / "logs")
        Path(log_dir).mkdir(exist_ok=True)

        board_path = str(run_dir / "board.md")
        return log_dir, board_path

    def _init_board(self, board_path: str, content: str) -> None:
        """共有ボードを初期化する。"""
        with open(board_path, "w", encoding="utf-8") as f:
            header = (
                f"# 共有ボード\n\n"
                f"作成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"---\n\n"
            )
            f.write(header + content)
        logger.info("共有ボード初期化: %s", board_path)

    def _run_sequential(
        self,
        plan: ProjectPlan,
        log_dir: str,
        board_path: str,
        event_callback,
    ) -> dict[str, dict]:
        """エージェントを逐次実行する。"""
        results: dict[str, dict] = {}

        for assignment in plan.agents:
            logger.info("エージェント開始: %s (%s)", assignment.agent_name, assignment.role)

            result = _run_single_agent(
                assignment=assignment,
                client=self.client,
                work_dir=self.work_dir,
                board_path=board_path,
                log_dir=log_dir,
                max_iterations=self.max_iterations,
                result_queue=None,
                event_callback=event_callback,
            )
            results[assignment.agent_name] = result

            if not result["success"]:
                logger.warning(
                    "エージェント %s が失敗: %s", assignment.agent_name, result["error"]
                )
                # エラーでも次のエージェントに続ける（ボードで情報共有済み）

        return results

    def _run_parallel(
        self,
        plan: ProjectPlan,
        log_dir: str,
        board_path: str,
        event_callback,
    ) -> dict[str, dict]:
        """
        エージェントを並行実行する。

        depends_on のあるエージェントは依存先の完了を待ってから起動する。
        """
        results: dict[str, dict] = {}
        completed: set[str] = set()
        result_queue: queue.Queue = queue.Queue()
        threads: dict[str, threading.Thread] = {}
        pending = list(plan.agents)

        while pending or threads:
            # 依存関係が解決済みのエージェントを起動
            ready = [
                a for a in pending
                if all(dep in completed for dep in a.depends_on)
            ]
            for assignment in ready:
                pending.remove(assignment)
                t = threading.Thread(
                    target=_run_single_agent,
                    kwargs={
                        "assignment": assignment,
                        "client": self.client,
                        "work_dir": self.work_dir,
                        "board_path": board_path,
                        "log_dir": log_dir,
                        "max_iterations": self.max_iterations,
                        "result_queue": result_queue,
                        "event_callback": event_callback,
                    },
                    daemon=True,
                )
                t.name = assignment.agent_name
                t.start()
                threads[assignment.agent_name] = t
                logger.info("並列エージェント起動: %s", assignment.agent_name)

            # 完了したスレッドを回収
            try:
                result = result_queue.get(timeout=0.5)
                agent_name = result["agent_name"]
                results[agent_name] = result
                completed.add(agent_name)
                if agent_name in threads:
                    threads[agent_name].join(timeout=5)
                    del threads[agent_name]
                logger.info("エージェント完了: %s", agent_name)
            except queue.Empty:
                pass

        return results

    def _write_summary(self, board_path: str, results: dict[str, dict]) -> None:
        """実行サマリーを共有ボードに追記する。"""
        lines = [
            "\n---\n",
            "## 実行サマリー\n",
            f"完了日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n",
            "| エージェント | 役割 | 結果 | 時間 |",
            "| --- | --- | --- | --- |",
        ]
        for name, r in results.items():
            status = "✅ 成功" if r["success"] else "❌ 失敗"
            elapsed = f"{r['elapsed']:.1f}s"
            lines.append(f"| {name} | {r['role']} | {status} | {elapsed} |")

        with open(board_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
