"""Redis connections and the names of every stream, channel and key we use.

Keeping them in one place makes the message flow easy to follow:

    producer --XADD--> ALERT_STREAM --worker group--> DB + matches
                                         |-- PUBLISH LIVE_CHANNEL (all alerts, for the dashboard)
                                         |-- PUBLISH user_channel(uid) (that user's matches)
                                         '-- XADD NOTIFY_STREAM --notifier group--> webhooks
                                                                     |-- ZADD NOTIFY_RETRY (backoff)
                                                                     '-- XADD NOTIFY_DLQ  (gave up)
"""
import redis
import redis.asyncio as aioredis

from .config import settings

ALERT_STREAM = "alerts:stream"
ALERT_GROUP = "matchers"
NOTIFY_STREAM = "notify:stream"
NOTIFY_GROUP = "notifiers"
NOTIFY_RETRY = "notify:retry"  # sorted set, score = unix time when due
NOTIFY_DLQ = "notify:dlq"
LIVE_CHANNEL = "alerts:live"
RULES_VERSION = "rules:version"
STREAM_MAXLEN = 200_000


def user_channel(user_id: int) -> str:
    return f"user:{user_id}:matches"


_sync: redis.Redis | None = None
_async: aioredis.Redis | None = None


def get_redis() -> redis.Redis:
    global _sync
    if _sync is None:
        _sync = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _sync


def get_async_redis() -> aioredis.Redis:
    global _async
    if _async is None:
        _async = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _async


def set_clients(sync_client=None, async_client=None) -> None:
    """Swap clients (used by tests to inject fakeredis)."""
    global _sync, _async
    _sync, _async = sync_client, async_client


def bump_rules_version(r: redis.Redis | None = None) -> None:
    """Tell workers that rules changed so they rebuild their index."""
    try:
        (r or get_redis()).incr(RULES_VERSION)
    except redis.RedisError:
        pass  # workers also refresh on a timer, so this is best effort


def ensure_group(r: redis.Redis, stream: str, group: str) -> None:
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
