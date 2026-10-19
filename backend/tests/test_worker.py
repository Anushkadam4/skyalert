import json

from app import bus
from app.auth import hash_password
from app.models import Alert, Match, Rule, User
from app.producer import JD_START, make_alert, make_population
from app.worker import AlertProcessor, parse_alert


def make_user_and_rules(session_factory, webhook=None):
    with session_factory() as db:
        user = User(email="ada@example.com", password_hash=hash_password("x" * 8))
        db.add(user)
        db.flush()
        bright = Rule(user_id=user.id, name="bright", conditions=[{"field": "magpsf", "op": "lt", "value": 18}],
                      webhook_url=webhook)
        cone = Rule(user_id=user.id, name="cone", conditions=[], cone_ra=100.0, cone_dec=10.0,
                    cone_radius_arcsec=60)
        off = Rule(user_id=user.id, name="disabled", conditions=[], active=False)
        db.add_all([bright, cone, off])
        db.commit()
        return user.id, bright.id, cone.id


def push(r, alert):
    r.xadd(bus.ALERT_STREAM, {"data": json.dumps(alert)})


def alert(candid, **kw):
    base = dict(candid=candid, object_id="ZTF26abcdefg", ra=50.0, dec=0.0, magpsf=19.0, sigmapsf=0.05,
                fid=2, rb=0.9, ndethist=1, jd=JD_START)
    return {**base, **kw}


def test_end_to_end_matching_and_fan_out(r, session_factory):
    user_id, bright_id, cone_id = make_user_and_rules(session_factory, webhook="https://example.com/h")
    pubsub = r.pubsub()
    pubsub.subscribe(bus.LIVE_CHANNEL, bus.user_channel(user_id))
    pubsub.get_message(timeout=0.1)
    pubsub.get_message(timeout=0.1)  # consume subscribe confirmations

    push(r, alert(1, magpsf=17.0))                 # matches "bright"
    push(r, alert(2, ra=100.0, dec=10.0 + 30 / 3600))  # matches "cone"
    push(r, alert(3))                              # matches nothing
    worker = AlertProcessor(session_factory, r, consumer="w1")
    worker.process_batch()  # pending backlog (empty) -> switches to new messages
    assert worker.process_batch(block_ms=10) == 3

    with session_factory() as db:
        assert db.query(Alert).count() == 3
        matches = {(m.rule_id, m.alert.candid) for m in db.query(Match).all()}
    assert matches == {(bright_id, 1), (cone_id, 2)}

    # Everything ACKed
    assert r.xpending(bus.ALERT_STREAM, bus.ALERT_GROUP)["pending"] == 0
    # Only the rule with a webhook enqueues a notification job
    jobs = [json.loads(f["job"]) for _, f in r.xrange(bus.NOTIFY_STREAM)]
    assert [(j["rule_name"], j["alert"]["candid"]) for j in jobs] == [("bright", 1)]
    # Live feed got all 3 alerts, the user channel got both matches
    msgs = [pubsub.get_message(timeout=0.1) for _ in range(5)]
    channels = [m["channel"] for m in msgs if m]
    assert channels.count(bus.LIVE_CHANNEL) == 3 and channels.count(bus.user_channel(user_id)) == 2


def test_redelivery_is_idempotent(r, session_factory):
    make_user_and_rules(session_factory)
    worker = AlertProcessor(session_factory, r, consumer="w1")
    for _ in range(2):  # the same alert arrives twice (e.g. producer retry)
        push(r, alert(7, magpsf=17.0))
    push(r, alert(8, magpsf=17.0))
    worker.process_batch()
    worker.process_batch(block_ms=10)
    worker.handle([parse_alert(json.dumps(alert(7, magpsf=17.0)))])  # and once more directly
    with session_factory() as db:
        assert db.query(Alert).count() == 2
        assert db.query(Match).count() == 2


def test_crash_recovery_rereads_pending(r, session_factory):
    make_user_and_rules(session_factory)
    bus.ensure_group(r, bus.ALERT_STREAM, bus.ALERT_GROUP)
    push(r, alert(11, magpsf=17.0))
    # Simulate a worker that read the message and died before ACKing
    r.xreadgroup(bus.ALERT_GROUP, "w1", {bus.ALERT_STREAM: ">"}, count=10)
    assert r.xpending(bus.ALERT_STREAM, bus.ALERT_GROUP)["pending"] == 1

    restarted = AlertProcessor(session_factory, r, consumer="w1")
    assert restarted.process_batch() == 1  # starts with its own pending messages
    assert r.xpending(bus.ALERT_STREAM, bus.ALERT_GROUP)["pending"] == 0

    # A different worker can also claim messages abandoned by a dead consumer
    push(r, alert(12, magpsf=17.0))
    r.xreadgroup(bus.ALERT_GROUP, "dead", {bus.ALERT_STREAM: ">"}, count=10)
    assert AlertProcessor(session_factory, r, consumer="w2").claim_stale(min_idle_ms=0) == 1
    with session_factory() as db:
        assert db.query(Alert).count() == 2


def test_malformed_messages_are_dropped_not_stuck(r, session_factory):
    make_user_and_rules(session_factory)
    r.xadd(bus.ALERT_STREAM, {"data": "not json"})
    r.xadd(bus.ALERT_STREAM, {"data": json.dumps({"candid": 1})})
    push(r, alert(5, dec=123))  # impossible declination
    push(r, alert(6))
    worker = AlertProcessor(session_factory, r, consumer="w1")
    worker.process_batch()
    assert worker.process_batch(block_ms=10) == 4
    assert r.xpending(bus.ALERT_STREAM, bus.ALERT_GROUP)["pending"] == 0
    with session_factory() as db:
        assert [a.candid for a in db.query(Alert).all()] == [6]


def test_rule_changes_picked_up_via_version(r, session_factory):
    user_id, *_ = make_user_and_rules(session_factory)
    worker = AlertProcessor(session_factory, r, consumer="w1", rules_refresh_s=3600)
    worker.refresh_rules()
    assert worker.index.size == 2  # disabled rule is not loaded
    with session_factory() as db:
        db.add(Rule(user_id=user_id, name="new", conditions=[]))
        db.commit()
    worker.refresh_rules()
    assert worker.index.size == 2  # version unchanged -> cached index kept
    bus.bump_rules_version(r)
    worker.refresh_rules()
    assert worker.index.size == 3


def test_simulated_alerts_are_valid():
    for obj in make_population(200, JD_START):
        a = parse_alert(json.dumps(make_alert(obj, JD_START + 1, 1)))
        assert 0 <= a["ra"] < 360 and -90 <= a["dec"] <= 90 and 0 <= a["rb"] <= 1
