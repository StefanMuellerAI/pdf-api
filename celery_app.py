from celery import Celery, signals
from config import *
import logging
import os

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Celery
celery = Celery(
    'pdf_api',
    broker=os.getenv('CELERY_BROKER_URL', REDIS_URL),
    backend=os.getenv('CELERY_RESULT_BACKEND', REDIS_URL),
    broker_connection_retry_on_startup=True
)

# Configure Celery
celery.conf.update(
    broker_transport_options={
        'visibility_timeout': REDIS_TIMEOUT,
        'fanout_prefix': True,
        'fanout_patterns': True
    },
    redis_socket_timeout=REDIS_TIMEOUT,
    redis_socket_connect_timeout=REDIS_TIMEOUT,
    broker_connection_retry=True,
    broker_connection_max_retries=MAX_RETRIES,
    worker_max_tasks_per_child=1,
    worker_prefetch_multiplier=1,
    worker_pool='solo',
    task_serializer='pickle',
    accept_content=['pickle', 'json'],
    result_serializer='pickle',
    task_default_queue='pdf_tasks',
    task_routes={
        'pdf_api.tasks.process_pdf': {'queue': 'pdf_tasks'}
    },
    worker_reset_tasks_at_start=True,
    task_reject_on_worker_lost=True,
    task_acks_late=True
)

# Queue purging function
@celery.task(name='pdf_api.tasks.purge_queue')
def purge_queue():
    """Purges the Celery queue on worker start."""
    try:
        celery.control.purge()
        logger.info("Successfully purged Celery queue")
    except Exception as e:
        logger.error(f"Error purging Celery queue: {e}")

# Worker startup signal
@signals.worker_ready.connect
def clean_at_start(sender=None, conf=None, **kwargs):
    """Executes when the worker starts."""
    purge_queue.delay() 