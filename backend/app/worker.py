"""Matcher worker: consumes the alert stream, stores alerts, evaluates rules.

Run several copies; Redis consumer groups split the stream between them.
Delivery is at-least-once: a message is ACKed only after its batch is
committed, and inserts are idempotent on `candid`, so a redelivered
message never creates duplicate alerts or matches.
"""
import argparse
import json
import logging
import os
import socket
import time

import redis
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from . import bus
from .db import SessionLocal, init_db
from .models import Alert, Match, Rule
from .rules import RuleIndex, compile_orm_rule

log = logging.getLogger("skyalert.worker")

ALERTS = Counter("worker_alerts_total", "Alerts consumed", ["result"])  # new / duplicate / invalid
MATCHES = Counter("worker_matches_total", "Rule matches produced")
BATCH_SECONDS = Histogram("worker_batch_seconds", "Time to process one batch")
MATCH_SECONDS = Histogram("worker_match_seconds", "Rule matching time per alert",
                          buckets=(1e-5, 5e-5, 1e-4, 5e-4, 1e-3, 5e-3, 1e-2, 5e-2))
RULES_LOADED = Gauge("worker_rules_loaded", "Active rules in the in-memory index")

ALERT_FIELDS = {
    "candid": int, "object_id": str, "ra": float, "dec": float, "magpsf": float,
    "sigmapsf": float, "fid": int, "rb": float, "ndethist": int, "jd": float,
}


def parse_alert(raw: str) -> dict:
    """Validate and coerce one alert payload. Raises ValueError if malformed."""
    data = json.loads(raw)
    try:
        alert = {k: cast(data[k]) for k, cast in ALERT_FIELDS.items()}
    except (KeyError, TypeError) as exc:
        raise ValueError(f"bad alert: {exc}") from exc
    if not (0 <= alert["ra"] < 360 and -90 <= alert["dec"] <= 90):
        raise ValueError("coordinates out of range")
    return alert


def alert_to_dict(a: Alert) -> dict:
    return {
        "id": a.id, "candid": a.candid, "object_id": a.object_id, "ra": a.ra, "dec": a.dec,
        "magpsf": a.magpsf, "sigmapsf": a.sigmapsf, "fid": a.fid, "rb": a.rb,
        "ndethist": a.ndethist, "jd": a.jd, "received_at": a.received_at.isoformat(),
    }


class AlertProcessor:
    def __init__(self, session_factory: sessionmaker, r: redis.Redis, consumer: str,
                 rules_refresh_s: float = 30.0, cell_deg: float = 1.0):
        self.Session = session_factory
        self.r = r
        self.consumer = consumer
        self.rules_refresh_s = rules_refresh_s
        self.cell_deg = cell_deg
        self.index = RuleIndex(cell_deg=cell_deg)
        self._rules_version: str | None = None
        self._rules_loaded_at: float | None = None  # None = never loaded
        self._read_id = "0"  # start by re-reading our own un-ACKed messages (crash recovery)
        bus.ensure_group(r, bus.ALERT_STREAM, bus.ALERT_GROUP)

    # ------------------------------------------------------------ rules

    def refresh_rules(self, force: bool = False) -> None:
        version = self.r.get(bus.RULES_VERSION)
        stale = (self._rules_loaded_at is None
                 or time.monotonic() - self._rules_loaded_at > self.rules_refresh_s)
        if not (force or stale or version != self._rules_version):
            return
        with self.Session() as db:
            rules = db.scalars(select(Rule).where(Rule.active.is_(True))).all()
            self.index = RuleIndex((compile_orm_rule(r) for r in rules), cell_deg=self.cell_deg)
        self._rules_version = version
        self._rules_loaded_at = time.monotonic()
        RULES_LOADED.set(self.index.size)
        log.info("loaded %d active rules (version %s)", self.index.size, version)

    # ------------------------------------------------------------ consuming

    def process_batch(self, count: int = 200, block_ms: int = 1000) -> int:
        """Read and handle one batch. Returns the number of stream entries handled."""
        resp = self.r.xreadgroup(bus.ALERT_GROUP, self.consumer, {bus.ALERT_STREAM: self._read_id},
                                 count=count, block=None if self._read_id == "0" else block_ms)
        entries = resp[0][1] if resp else []
        if not entries:
            if self._read_id == "0":
                self._read_id = ">"  # backlog of pending messages is done; switch to new ones
            return 0
        return self._process_entries(entries)

    def claim_stale(self, min_idle_ms: int = 60_000, count: int = 200) -> int:
        """Take over messages left un-ACKed by a worker that died."""
        _next, entries, *_ = self.r.xautoclaim(bus.ALERT_STREAM, bus.ALERT_GROUP, self.consumer,
                                               min_idle_time=min_idle_ms, start_id="0-0", count=count)
        return self._process_entries(entries) if entries else 0

    def _process_entries(self, entries) -> int:
        with BATCH_SECONDS.time():
            self.refresh_rules()
            ids, alerts = [], []
            for msg_id, fields in entries:
                ids.append(msg_id)
                if fields is None:  # entry was trimmed from the stream before we read it
                    continue
                try:
                    alerts.append(parse_alert(fields["data"]))
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    ALERTS.labels("invalid").inc()
                    log.warning("dropping malformed message %s: %s", msg_id, exc)
            if alerts:
                try:
                    self.handle(alerts)
                except IntegrityError:
                    # Most likely a rule was deleted after we loaded the index. Reload and retry once.
                    self.refresh_rules(force=True)
                    self.handle(alerts)
            self.r.xack(bus.ALERT_STREAM, bus.ALERT_GROUP, *ids)
        return len(entries)

    def handle(self, alerts: list[dict]) -> list[Match]:
        """Store new alerts, find matches, then publish/fan out. Idempotent on candid."""
        with self.Session() as db:
            candids = [a["candid"] for a in alerts]
            existing = set(db.scalars(select(Alert.candid).where(Alert.candid.in_(candids))))
            new_payloads, seen = [], set()
            for a in alerts:
                if a["candid"] in existing or a["candid"] in seen:
                    ALERTS.labels("duplicate").inc()
                    continue
                seen.add(a["candid"])
                new_payloads.append(a)
            rows = [Alert(**a) for a in new_payloads]
            db.add_all(rows)
            db.flush()  # assigns primary keys

            pairs = []
            for row, payload in zip(rows, new_payloads):
                t0 = time.perf_counter()
                hits = self.index.match(payload)
                MATCH_SECONDS.observe(time.perf_counter() - t0)
                pairs.extend((rule, row) for rule in hits)
            matches = [Match(rule_id=rule.id, alert_id=row.id, user_id=rule.user_id) for rule, row in pairs]
            db.add_all(matches)
            db.commit()

        ALERTS.labels("new").inc(len(rows))
        MATCHES.inc(len(matches))
        self._fan_out(rows, pairs, matches)
        return matches

    def _fan_out(self, rows: list[Alert], pairs, matches: list[Match]) -> None:
        pipe = self.r.pipeline(transaction=False)
        for row in rows:
            pipe.publish(bus.LIVE_CHANNEL, json.dumps(alert_to_dict(row)))
        for (rule, row), match in zip(pairs, matches):
            payload = {"match_id": match.id, "rule_id": rule.id, "rule_name": rule.name,
                       "alert": alert_to_dict(row)}
            pipe.publish(bus.user_channel(rule.user_id), json.dumps(payload))
            if rule.webhook_url:
                job = {**payload, "user_id": rule.user_id, "url": rule.webhook_url, "attempt": 0}
                pipe.xadd(bus.NOTIFY_STREAM, {"job": json.dumps(job)}, maxlen=bus.STREAM_MAXLEN, approximate=True)
        pipe.execute()

    def run_forever(self, claim_every_s: float = 30.0) -> None:
        last_claim = time.monotonic()
        while True:
            try:
                self.process_batch()
                if time.monotonic() - last_claim > claim_every_s:
                    self.claim_stale()
                    last_claim = time.monotonic()
            except redis.ConnectionError:
                log.exception("redis unavailable; retrying in 2s")
                time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="SkyAlert matcher worker")
    parser.add_argument("--name", default=os.getenv("WORKER_NAME", socket.gethostname()))
    parser.add_argument("--metrics-port", type=int, default=int(os.getenv("METRICS_PORT", "9100")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    start_http_server(args.metrics_port)
    log.info("worker %s starting", args.name)
    AlertProcessor(SessionLocal, bus.get_redis(), consumer=args.name).run_forever()


if __name__ == "__main__":
    main()
