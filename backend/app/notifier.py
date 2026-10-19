"""Webhook notifier: delivers match notifications with retries and a DLQ.

Failed deliveries are not retried by sleeping in the worker (that would
block every other job). Instead they go into a Redis sorted set scored by
the time they become due, and a small loop moves due jobs back onto the
stream. After `max_attempts` a job goes to the dead-letter stream.
"""
import argparse
import ipaddress
import json
import logging
import os
import random
import socket
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
import redis
from prometheus_client import Counter, start_http_server

from . import bus
from .config import settings

log = logging.getLogger("skyalert.notifier")

DELIVERIES = Counter("notifier_deliveries_total", "Webhook delivery outcomes", ["result"])


def is_public_url(url: str, resolver: Callable = socket.getaddrinfo) -> bool:
    """Block webhooks to internal addresses (SSRF protection)."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        infos = resolver(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def backoff_seconds(attempt: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Exponential backoff with full jitter: 2s, 4s, 8s... capped at 5 minutes."""
    return random.uniform(0, min(cap, base * 2 ** attempt))


class Notifier:
    def __init__(self, r: redis.Redis, client: httpx.Client, consumer: str,
                 max_attempts: int = settings.webhook_max_attempts,
                 per_user_per_minute: int = settings.webhook_per_user_per_minute,
                 allow_private: bool = settings.allow_private_webhooks,
                 resolver: Callable = socket.getaddrinfo):
        self.r = r
        self.client = client
        self.consumer = consumer
        self.max_attempts = max_attempts
        self.per_user_per_minute = per_user_per_minute
        self.allow_private = allow_private
        self.resolver = resolver
        bus.ensure_group(r, bus.NOTIFY_STREAM, bus.NOTIFY_GROUP)

    # ------------------------------------------------------------ retry queue

    def schedule_retry(self, job: dict, delay_s: float) -> None:
        self.r.zadd(bus.NOTIFY_RETRY, {json.dumps(job, sort_keys=True): time.time() + delay_s})

    def move_due_retries(self, now: float | None = None, limit: int = 100) -> int:
        now = time.time() if now is None else now
        moved = 0
        for member in self.r.zrangebyscore(bus.NOTIFY_RETRY, 0, now, start=0, num=limit):
            # ZREM returns 1 only for the notifier that wins the race, so each job moves once.
            if self.r.zrem(bus.NOTIFY_RETRY, member):
                self.r.xadd(bus.NOTIFY_STREAM, {"job": member}, maxlen=bus.STREAM_MAXLEN, approximate=True)
                moved += 1
        return moved

    def dead_letter(self, job: dict, reason: str) -> None:
        DELIVERIES.labels("dead_letter").inc()
        self.r.xadd(bus.NOTIFY_DLQ, {"job": json.dumps(job), "reason": reason}, maxlen=50_000, approximate=True)
        log.warning("dead-lettered match %s: %s", job.get("match_id"), reason)

    # ------------------------------------------------------------ delivery

    def _over_user_limit(self, user_id: int) -> bool:
        key = f"nrl:{user_id}:{int(time.time() // 60)}"
        count = self.r.incr(key)
        if count == 1:
            self.r.expire(key, 60)
        return count > self.per_user_per_minute

    def deliver(self, job: dict) -> str:
        """Try one delivery. Returns 'ok', 'retry', 'deferred' or 'dead_letter'."""
        url = job["url"]
        if not self.allow_private and not is_public_url(url, self.resolver):
            self.dead_letter(job, "webhook resolves to a non-public address")
            return "dead_letter"

        if self._over_user_limit(job["user_id"]):
            DELIVERIES.labels("deferred").inc()
            self.schedule_retry(job, 60 - time.time() % 60 + 1)  # next window; not counted as an attempt
            return "deferred"

        body = {k: job[k] for k in ("match_id", "rule_id", "rule_name", "alert")}
        try:
            resp = self.client.post(url, json=body, timeout=5.0,
                                    headers={"User-Agent": "SkyAlert/1.0", "X-SkyAlert-Attempt": str(job["attempt"] + 1)})
            if resp.status_code < 300:
                DELIVERIES.labels("ok").inc()
                return "ok"
            retryable = resp.status_code >= 500 or resp.status_code == 429
            reason = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            retryable, reason = True, f"{type(exc).__name__}: {exc}"

        attempt = job["attempt"] + 1
        if not retryable or attempt >= self.max_attempts:
            self.dead_letter({**job, "attempt": attempt}, reason)
            return "dead_letter"
        DELIVERIES.labels("retry").inc()
        self.schedule_retry({**job, "attempt": attempt}, backoff_seconds(attempt))
        return "retry"

    def process_batch(self, count: int = 50, block_ms: int = 1000) -> int:
        self.move_due_retries()
        resp = self.r.xreadgroup(bus.NOTIFY_GROUP, self.consumer, {bus.NOTIFY_STREAM: ">"},
                                 count=count, block=block_ms)
        entries = resp[0][1] if resp else []
        for msg_id, fields in entries:
            try:
                self.deliver(json.loads(fields["job"]))
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                log.warning("dropping malformed job %s: %s", msg_id, exc)
            self.r.xack(bus.NOTIFY_STREAM, bus.NOTIFY_GROUP, msg_id)
        return len(entries)

    def run_forever(self) -> None:
        while True:
            try:
                self.process_batch()
            except redis.ConnectionError:
                log.exception("redis unavailable; retrying in 2s")
                time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="SkyAlert webhook notifier")
    parser.add_argument("--name", default=os.getenv("WORKER_NAME", socket.gethostname()))
    parser.add_argument("--metrics-port", type=int, default=int(os.getenv("METRICS_PORT", "9101")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    start_http_server(args.metrics_port)
    with httpx.Client(follow_redirects=False) as client:
        Notifier(bus.get_redis(), client, consumer=args.name).run_forever()


if __name__ == "__main__":
    main()
