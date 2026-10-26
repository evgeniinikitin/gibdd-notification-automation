from __future__ import annotations

from celery import Celery

from .config import get_settings

settings = get_settings()
celery_app = Celery(
    "buscript",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.automation.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Europe/Moscow",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "socket_connect_timeout": 3,
        "socket_timeout": 5,
        "retry_on_timeout": True,
    },
)
