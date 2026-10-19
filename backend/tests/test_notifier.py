import json
import socket

import httpx
import pytest

from app import bus
from app.notifier import Notifier, is_public_url

PUBLIC = lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]  # noqa: E731
PRIVATE = lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]  # noqa: E731


def job(**kw):
    base = {"match_id": 1, "rule_id": 2, "rule_name": "bright", "user_id": 3, "url": "https://hooks.example.com/x",
            "attempt": 0, "alert": {"candid": 42}}
    return {**base, **kw}


def make_notifier(r, handler, resolver=PUBLIC, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Notifier(r, client, consumer="n1", resolver=resolver, allow_private=False,
                    max_attempts=kw.get("max_attempts", 3), per_user_per_minute=kw.get("per_user", 100))


def test_successful_delivery_sends_payload(r):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200)

    n = make_notifier(r, handler)
    r.xadd(bus.NOTIFY_STREAM, {"job": json.dumps(job())})
    assert n.process_batch(block_ms=10) == 1
    assert seen == [{"match_id": 1, "rule_id": 2, "rule_name": "bright", "alert": {"candid": 42}}]
    assert r.zcard(bus.NOTIFY_RETRY) == 0 and r.xlen(bus.NOTIFY_DLQ) == 0


def test_server_errors_retry_with_backoff_then_dead_letter(r):
    n = make_notifier(r, lambda req: httpx.Response(503), max_attempts=3)
    assert n.deliver(job()) == "retry"
    assert r.zcard(bus.NOTIFY_RETRY) == 1

    # Pretend time passed: due retries move back to the stream with attempt incremented
    assert n.move_due_retries(now=1e12) == 1
    (_, fields), = r.xrange(bus.NOTIFY_STREAM)
    retried = json.loads(fields["job"])
    assert retried["attempt"] == 1
    assert n.deliver(retried) == "retry"
    assert n.deliver({**retried, "attempt": 2}) == "dead_letter"
    (_, dlq), = r.xrange(bus.NOTIFY_DLQ)
    assert dlq["reason"] == "HTTP 503"


def test_network_errors_are_retried(r):
    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    assert make_notifier(r, handler).deliver(job()) == "retry"


def test_client_errors_go_straight_to_dlq(r):
    assert make_notifier(r, lambda req: httpx.Response(404)).deliver(job()) == "dead_letter"
    assert r.zcard(bus.NOTIFY_RETRY) == 0


def test_ssrf_blocked(r):
    calls = []
    n = make_notifier(r, lambda req: calls.append(req) or httpx.Response(200), resolver=PRIVATE)
    assert n.deliver(job(url="http://internal-service/admin")) == "dead_letter"
    assert calls == []


@pytest.mark.parametrize("ip,ok", [("8.8.8.8", True), ("127.0.0.1", False), ("169.254.169.254", False),
                                   ("192.168.1.1", False), ("::1", False)])
def test_is_public_url(ip, ok):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    assert is_public_url("http://x.test/", lambda h, p: [(family, 1, 6, "", (ip, 0))]) is ok


def test_per_user_rate_limit_defers_without_using_an_attempt(r):
    n = make_notifier(r, lambda req: httpx.Response(200), per_user=2)
    assert [n.deliver(job(match_id=i)) for i in range(3)] == ["ok", "ok", "deferred"]
    (member,) = r.zrange(bus.NOTIFY_RETRY, 0, -1)
    assert json.loads(member)["attempt"] == 0


def test_retry_moves_only_once(r):
    n1 = make_notifier(r, lambda req: httpx.Response(200))
    n2 = make_notifier(r, lambda req: httpx.Response(200))
    n1.schedule_retry(job(), 0)
    assert n1.move_due_retries(now=1e12) + n2.move_due_retries(now=1e12) == 1
