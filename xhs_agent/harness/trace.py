"""JSONL Trace 记录器：记录每次任务运行的完整事件流。

每个会话写入独立的 traces/{session_id}.jsonl，作为调试、统计以及后续 Journal / Resume / Eval 的基础。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return repr(obj)


class TraceLogger:
    """以 JSONL 逐行追加事件到 traces/{session_id}.jsonl。"""

    def __init__(self, session_id: str, traces_dir: Path | str = Path("traces")) -> None:
        self.session_id = session_id
        self.traces_dir = Path(traces_dir)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.traces_dir / f"{session_id}.jsonl"
        self._events_count = 0

    @property
    def events_count(self) -> int:
        return self._events_count

    def log(self, event_type: str, *, step: int | None = None, **fields: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": _utc_now_iso(),
            "session": self.session_id,
            "type": event_type,
        }
        if step is not None:
            record["step"] = step
        record.update(fields)

        line = json.dumps(record, ensure_ascii=False, default=_json_default)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        self._events_count += 1
        return record

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]
