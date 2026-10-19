import time

from app.db import SessionLocal
from app.models import Alert, Match, Rule
from conftest import register

RULE = {
    "name": "Bright near M31",
    "conditions": [{"field": "magpsf", "op": "lt", "value": 19}, {"field": "rb", "op": "gte", "value": 0.7}],
    "cone": {"ra": 10.6847, "dec": 41.269, "radius_arcsec": 3600},
    "webhook_url": "https://example.com/hook",
}


def seed_alerts(n):
    with SessionLocal() as db:
        db.add_all(Alert(candid=1000 + i, object_id=f"ZTF26obj{i % 3}", ra=10.0, dec=41.0, magpsf=17 + i * 0.1,
                         sigmapsf=0.05, fid=1 + i % 3, rb=0.9, ndethist=i, jd=2461330.5 + i) for i in range(n))
        db.commit()


def test_register_login_me(client):
    register(client, "grace@example.com", "pass-word-1")
    assert client.post("/api/auth/register", json={"email": "grace@example.com", "password": "pass-word-1"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "grace@example.com", "password": "wrong-pass"}).status_code == 401
    token = client.post("/api/auth/login", json={"email": "Grace@example.com", "password": "pass-word-1"}).json()["access_token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "grace@example.com"


def test_requires_auth(client):
    assert client.get("/api/rules").status_code == 401
    assert client.get("/api/rules", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_rule_crud_and_version_bump(client, auth, r):
    created = client.post("/api/rules", json=RULE, headers=auth)
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["cone"]["radius_arcsec"] == 3600
    assert r.get("rules:version") == "1"

    updated = client.put(f"/api/rules/{rule['id']}", json={**RULE, "name": "renamed", "cone": None}, headers=auth)
    assert updated.json()["name"] == "renamed" and updated.json()["cone"] is None
    assert [x["name"] for x in client.get("/api/rules", headers=auth).json()] == ["renamed"]

    assert client.delete(f"/api/rules/{rule['id']}", headers=auth).status_code == 204
    assert client.get("/api/rules", headers=auth).json() == []
    assert r.get("rules:version") == "3"


def test_rule_validation(client, auth):
    bad = [
        {**RULE, "conditions": [{"field": "password", "op": "eq", "value": 1}]},
        {**RULE, "conditions": [{"field": "magpsf", "op": "like", "value": 1}]},
        {**RULE, "cone": {"ra": 400, "dec": 0, "radius_arcsec": 10}},
        {**RULE, "webhook_url": "ftp://example.com"},
        {**RULE, "name": ""},
    ]
    for body in bad:
        assert client.post("/api/rules", json=body, headers=auth).status_code == 422, body


def test_users_cannot_touch_each_others_rules(client, auth):
    rule_id = client.post("/api/rules", json=RULE, headers=auth).json()["id"]
    other = register(client, "eve@example.com", "eve-password")
    assert client.get("/api/rules", headers=other).json() == []
    assert client.put(f"/api/rules/{rule_id}", json=RULE, headers=other).status_code == 404
    assert client.delete(f"/api/rules/{rule_id}", headers=other).status_code == 404


def test_alerts_cursor_pagination_and_filters(client, auth):
    seed_alerts(25)
    page1 = client.get("/api/alerts?limit=10", headers=auth).json()
    page2 = client.get(f"/api/alerts?limit=10&cursor={page1['next_cursor']}", headers=auth).json()
    page3 = client.get(f"/api/alerts?limit=10&cursor={page2['next_cursor']}", headers=auth).json()
    ids = [a["id"] for p in (page1, page2, page3) for a in p["items"]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 25
    assert page3["next_cursor"] is None

    bright = client.get("/api/alerts?max_mag=17.5&limit=200", headers=auth).json()["items"]
    assert bright and all(a["magpsf"] <= 17.5 for a in bright)
    g_band = client.get("/api/alerts?fid=1&limit=200", headers=auth).json()["items"]
    assert g_band and all(a["fid"] == 1 for a in g_band)

    lc = client.get("/api/objects/ZTF26obj0/lightcurve", headers=auth).json()
    assert [a["jd"] for a in lc] == sorted(a["jd"] for a in lc)
    assert client.get("/api/objects/nope/lightcurve", headers=auth).status_code == 404


def test_matches_only_show_own(client, auth):
    seed_alerts(3)
    rule_id = client.post("/api/rules", json=RULE, headers=auth).json()["id"]
    user_id = client.get("/api/me", headers=auth).json()["id"]
    with SessionLocal() as db:
        db.add(Match(rule_id=rule_id, alert_id=1, user_id=user_id))
        db.commit()
    mine = client.get("/api/matches", headers=auth).json()["items"]
    assert len(mine) == 1 and mine[0]["rule_name"] == RULE["name"] and mine[0]["alert"]["candid"] == 1000
    other = register(client, "eve@example.com", "eve-password")
    assert client.get("/api/matches", headers=other).json()["items"] == []

    stats = client.get("/api/stats", headers=auth).json()
    assert stats["my_rules"] == 1 and stats["my_matches"] == 1 and stats["total_alerts"] == 3


def test_rate_limit(client, r):
    r.set(f"rl:testclient:{int(time.time() // 60)}", 1000)
    resp = client.post("/api/auth/login", json={"email": "a@b.co", "password": "whatever1"})
    assert resp.status_code == 429
    assert client.get("/healthz").status_code == 200  # health checks are not limited


def test_rule_limit(client, auth, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "MAX_RULES_PER_USER", 2)
    for _ in range(2):
        assert client.post("/api/rules", json=RULE, headers=auth).status_code == 201
    assert client.post("/api/rules", json=RULE, headers=auth).status_code == 400
    with SessionLocal() as db:
        assert db.query(Rule).count() == 2


def test_metrics_endpoint(client):
    client.get("/healthz")
    body = client.get("/metrics").text
    assert "http_requests_total" in body
