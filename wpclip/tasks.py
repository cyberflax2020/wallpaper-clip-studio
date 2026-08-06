"""任务状态机：显式状态与合法迁移，支持取消（控制点式 stop 回调）。

状态：queued → running → {done, failed, cancelled}。
非法迁移抛 InvalidTransition；取消通过 threading.Event 注入到编码循环的控制点。
"""
from __future__ import annotations

import threading
import time
import uuid


class InvalidTransition(RuntimeError):
    """非法的状态迁移。"""


class JobCancelled(RuntimeError):
    """任务被取消（在控制点抛出）。"""


# 合法迁移表：state -> 允许的目标状态集合
TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"done", "failed", "cancelled"},
    "done": set(),
    "failed": {"queued"},   # 允许重试（回到 queued 再起）
    "cancelled": {"queued"},
}


class Job:
    def __init__(self, name: str):
        self.id = uuid.uuid4().hex[:10]
        self.name = name
        self.status = "queued"
        self.log: list[str] = []
        self.result = None
        self.error = None
        self.created = time.time()
        self._stop = threading.Event()

    def stop_requested(self) -> bool:
        return self._stop.is_set()

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "status": self.status,
                "log": self.log[-200:], "result": self.result, "error": self.error}


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, name: str) -> Job:
        job = Job(name)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def transition(self, job: Job, to: str) -> None:
        if to not in TRANSITIONS.get(job.status, set()):
            raise InvalidTransition(f"{job.status} -> {to}")
        job.status = to

    def start(self, job: Job) -> None:
        self.transition(job, "running")

    def finish(self, job: Job, result) -> None:
        self.transition(job, "done")
        job.result = result

    def fail(self, job: Job, err: str) -> None:
        self.transition(job, "failed")
        job.error = err

    def cancel(self, job: Job) -> None:
        if job.status in ("done",):
            raise InvalidTransition("done -> cancelled")
        job._stop.set()
        if job.status in ("queued", "running"):
            job.status = "cancelled"
