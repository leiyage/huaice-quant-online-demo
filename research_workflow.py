"""Small durable task journal for cross-department research workflows.

This module stores orchestration state only. It does not implement or replace
the quant, collection, or intelligence engines.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STAGES = ("intelligence", "risk", "quant", "trading", "report")
STEP_STATES = {"completed", "skipped", "blocked", "failed"}
TASK_STATES = {"running", "blocked", "failed", "completed"}
MAX_JSON_BYTES = 512 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("工作流数据过大。")
    return encoded


class WorkflowStore:
    """SQLite-backed workflow journal with an explicit, ordered state machine."""

    def __init__(self, path: Path | str):
        self._uri = str(path) == ":memory:"
        if self._uri:
            self.path = f"file:workflow-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._anchor = sqlite3.connect(self.path, uri=True, timeout=5)
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workflow_tasks (
                    task_id TEXT PRIMARY KEY,
                    workflow_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS workflow_logs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES workflow_tasks(task_id),
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workflow_logs_task_seq
                    ON workflow_logs(task_id, seq);
                CREATE INDEX IF NOT EXISTS workflow_tasks_updated
                    ON workflow_tasks(updated_at DESC);
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, uri=self._uri)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"], "workflow_type": row["workflow_type"],
            "title": row["title"], "status": row["status"], "stage": row["stage"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "input": json.loads(row["input_json"]), "result": json.loads(row["result_json"]),
        }

    def _log(self, db: sqlite3.Connection, task_id: str, stage: str, status: str,
             message: str, details: dict[str, Any] | None = None) -> None:
        db.execute("INSERT INTO workflow_logs(task_id,stage,status,message,details_json,created_at) VALUES(?,?,?,?,?,?)",
                   (task_id, stage, status, message[:1000], _json(details or {}), _now()))

    def create(self, workflow_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if workflow_type != "oil_supply_demo":
            raise ValueError("当前仅开放原油供应冲击 DEMO 工作流。")
        task_id, now = uuid.uuid4().hex, _now()
        task_input = payload or {"event_id": "DEMO-008", "is_demo": True}
        if not isinstance(task_input, dict):
            raise ValueError("工作流输入必须是 JSON 对象。")
        if task_input.get("event_id") != "DEMO-008" or task_input.get("is_demo") is not True:
            raise ValueError("该工作流只接受明确标记的 DEMO-008 事件。")
        input_json = _json(task_input)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,?,?,?)",
                       (task_id, workflow_type, "国际原油供应冲击研究", "running", STAGES[0], now, now, input_json, "{}"))
            self._log(db, task_id, STAGES[0], "running", "任务已创建；情报识别使用本地 DEMO 事件。",
                      {"event_id": "DEMO-008", "is_demo": True})
        return self.get(task_id)

    def advance(self, task_id: str, stage: str, status: str, message: str,
                result: dict[str, Any] | None = None) -> dict[str, Any]:
        if stage not in STAGES or status not in STEP_STATES:
            raise ValueError("无效的研究阶段或阶段状态。")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("阶段日志不能为空。")
        details_json = _json(result or {})
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("没有找到该研究任务。")
            if row["status"] != "running" or row["stage"] != stage:
                raise RuntimeError(f"任务当前阶段为 {row['stage']}（{row['status']}），不能提交 {stage}。")
            current_result = json.loads(row["result_json"])
            current_result[stage] = json.loads(details_json)
            index = STAGES.index(stage)
            is_terminal = stage == STAGES[-1] and status == "completed"
            next_stage = STAGES[index + 1] if status in ("completed", "skipped") and not is_terminal else stage
            task_status = "completed" if is_terminal else ("running" if status in ("completed", "skipped") else status)
            db.execute("UPDATE workflow_tasks SET status=?,stage=?,updated_at=?,result_json=? WHERE task_id=?",
                       (task_status, next_stage, now, _json(current_result), task_id))
            self._log(db, task_id, stage, status, message, json.loads(details_json))
            if task_status == "running" and next_stage != stage:
                self._log(db, task_id, next_stage, "running", f"进入{next_stage}阶段。")
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("没有找到该研究任务。")
            task = self._task(row)
            task["logs"] = [dict(item) | {"details": json.loads(item["details_json"])}
                            for item in db.execute("SELECT * FROM workflow_logs WHERE task_id=? ORDER BY seq", (task_id,))]
            for item in task["logs"]:
                item.pop("details_json", None)
            return task

    def list(self, limit: int = 12) -> list[dict[str, Any]]:
        limit = max(1, min(50, int(limit)))
        with self._connect() as db:
            rows = db.execute("SELECT * FROM workflow_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            return [self._task(row) for row in rows]
