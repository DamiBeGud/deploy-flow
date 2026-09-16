import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.analysis.jobs import execute
from app.config import settings

broker = RedisBroker(url=settings().redis_url, socket_connect_timeout=3, socket_timeout=3)
dramatiq.set_broker(broker)


@dramatiq.actor(queue_name="analysis", max_retries=0, time_limit=310_000)
def analyze(run_id: str):
    # PostgreSQL owns retries and fencing; broker retries are deliberately disabled.
    execute(run_id)
