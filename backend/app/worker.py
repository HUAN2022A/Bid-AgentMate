"""Celery worker：只当执行器（Q22 定稿），任务状态机由 services 自管。"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "bidagentmate",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(task_track_started=True, worker_max_tasks_per_child=50)


@celery_app.task(name="app.worker.parse_tender")
def parse_tender(project_id: int) -> None:
    from app.services.parse_service import run_parse

    run_parse(project_id)
