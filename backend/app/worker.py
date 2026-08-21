"""Celery worker：只当执行器（Q22 定稿），任务状态机由 services 自管。"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "bidagentmate",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(task_track_started=True, worker_max_tasks_per_child=50)


@celery_app.task(name="app.worker.analyze_tender")
def analyze_tender(project_id: int) -> None:
    from app.services.analyze_service import run_analyze

    run_analyze(project_id)


@celery_app.task(name="app.worker.draft_chapter")
def draft_chapter(project_id: int, chapter_id: int) -> None:
    from app.services.draft_service import run_draft_chapter

    run_draft_chapter(project_id, chapter_id)
