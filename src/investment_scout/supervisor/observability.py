"""관측성: 결정 로그는 State 밖(외부 JSONL·LangSmith)에 두고 trace_id로 연결.

- out/logs/{trace_id}.jsonl : {trace_id, ts, node, decision, reason} 한 줄씩 (라우팅 사유·재작업 판단·품질 verdict)
- LangSmith : LANGSMITH_API_KEY가 있으면 추적을 켜고, 실행 config의 metadata.trace_id로 같은 키를 남김
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

LOG_DIR = Path("out/logs")


def new_trace_id() -> str:
    return f"inv-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def configure_tracing() -> bool:
    """키가 있을 때만 LangSmith 추적 활성화 (키 없이 켜면 매 호출 경고가 발생)."""
    enabled = bool(os.getenv("LANGSMITH_API_KEY", "").strip()) and \
        os.getenv("LANGSMITH_TRACING", "true").lower() == "true"
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    return enabled


class DecisionLog:
    """외부 결정 로그 (State를 키우지 않도록 append-only 파일)."""

    def __init__(self, trace_id: str, directory: Path = LOG_DIR):
        self.trace_id = trace_id
        self.path = Path(directory) / f"{trace_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, node: str, decision: str, reason: str, **extra) -> None:
        record = {"trace_id": self.trace_id, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "node": node, "decision": decision, "reason": reason, **extra}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
