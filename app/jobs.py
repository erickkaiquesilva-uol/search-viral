"""Execução em segundo plano com progresso, para buscas que levam de 10s a alguns minutos.

Fica em memória: ao reiniciar o servidor, jobs em andamento se perdem (os
resultados concluídos já estão salvos no SQLite).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
MAX_AGE = 3600


def submit(fn, *args, **kwargs) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "progress": [], "search_id": None, "error": None, "started": time.time()}
    with _lock:
        _cleanup()
        _jobs[job_id] = job

    def progress(msg: str) -> None:
        job["progress"].append(msg)

    def runner():
        try:
            job["search_id"] = fn(*args, progress=progress, **kwargs)
            job["status"] = "done"
        except Exception as exc:
            log.exception("Job %s falhou", job_id)
            job["status"], job["error"] = "error", str(exc)

    _pool.submit(runner)
    return job_id


def get(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def _cleanup() -> None:
    now = time.time()
    for k in [k for k, j in _jobs.items() if j["status"] != "running" and now - j["started"] > MAX_AGE]:
        _jobs.pop(k, None)
