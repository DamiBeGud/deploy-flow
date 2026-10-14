import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.analysis.jobs import execute
from app.config import settings
from app.deployments.jobs import execute as execute_delivery
from app.integrations.github.jobs import execute as execute_github

broker = RedisBroker(url=settings().redis_url, socket_connect_timeout=3, socket_timeout=3)
dramatiq.set_broker(broker)


@dramatiq.actor(queue_name="analysis", max_retries=0, time_limit=310_000)
def analyze(run_id: str):
    # PostgreSQL owns retries and fencing; broker retries are deliberately disabled.
    execute(run_id)


@dramatiq.actor(queue_name="github", max_retries=0, time_limit=310_000)
def github_job(job_id: str):
    execute_github(job_id)


@dramatiq.actor(queue_name="delivery", max_retries=0, time_limit=310_000)
def delivery_job(job_id: str):
    execute_delivery(job_id)
