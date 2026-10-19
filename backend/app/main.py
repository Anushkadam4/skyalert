"""HTTP + WebSocket API."""
import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

import redis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from . import bus
from .auth import create_access_token, decode_token, get_current_user, hash_password, verify_password
from .config import settings
from .db import get_db, init_db
from .models import Alert, Match, Rule, User
from .schemas import AlertOut, Credentials, MatchOut, Page, RuleIn, RuleOut, Stats, Token, UserOut

log = logging.getLogger("skyalert.api")

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("http_request_seconds", "HTTP request latency", ["method", "route"])
MAX_RULES_PER_USER = 100


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="SkyAlert", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- middleware

@app.middleware("http")
async def rate_limit_and_metrics(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        # Fixed-window limiter: one Redis counter per client per minute.
        client = request.client.host if request.client else "unknown"
        key = f"rl:{client}:{int(time.time() // 60)}"
        try:
            r = bus.get_async_redis()
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, 60)
            if count > settings.rate_limit_per_minute:
                return JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                                    headers={"Retry-After": "60"})
        except redis.RedisError:
            pass  # fail open: an outage in Redis should not take the API down

    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    route_path = getattr(route, "path", "unmatched")
    LATENCY.labels(request.method, route_path).observe(time.perf_counter() - start)
    REQUESTS.labels(request.method, route_path, response.status_code).inc()
    return response


# ---------------------------------------------------------------- health

@app.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    db.execute(select(1))
    redis_ok = True
    try:
        bus.get_redis().ping()
    except redis.RedisError:
        redis_ok = False
    return {"status": "ok", "redis": redis_ok}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------- auth

@app.post("/api/auth/register", response_model=Token, status_code=201)
def register(body: Credentials, db: Session = Depends(get_db)):
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    return Token(access_token=create_access_token(user.id))


@app.post("/api/auth/login", response_model=Token)
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")
    return Token(access_token=create_access_token(user.id))


@app.get("/api/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ---------------------------------------------------------------- rules

def _own_rule(db: Session, user: User, rule_id: int) -> Rule:
    rule = db.get(Rule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return rule


def _apply(rule: Rule, body: RuleIn) -> None:
    rule.name = body.name
    rule.conditions = [c.model_dump() for c in body.conditions]
    rule.cone_ra = body.cone.ra if body.cone else None
    rule.cone_dec = body.cone.dec if body.cone else None
    rule.cone_radius_arcsec = body.cone.radius_arcsec if body.cone else None
    rule.webhook_url = body.webhook_url
    rule.active = body.active


@app.get("/api/rules", response_model=list[RuleOut])
def list_rules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rules = db.scalars(select(Rule).where(Rule.user_id == user.id).order_by(Rule.id)).all()
    return [RuleOut.from_orm_rule(r) for r in rules]


@app.post("/api/rules", response_model=RuleOut, status_code=201)
def create_rule(body: RuleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(Rule).where(Rule.user_id == user.id))
    if count >= MAX_RULES_PER_USER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"limit of {MAX_RULES_PER_USER} rules reached")
    rule = Rule(user_id=user.id)
    _apply(rule, body)
    db.add(rule)
    db.commit()
    bus.bump_rules_version()
    return RuleOut.from_orm_rule(rule)


@app.put("/api/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, body: RuleIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    rule = _own_rule(db, user, rule_id)
    _apply(rule, body)
    db.commit()
    bus.bump_rules_version()
    return RuleOut.from_orm_rule(rule)


@app.delete("/api/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_own_rule(db, user, rule_id))
    db.commit()
    bus.bump_rules_version()


# ---------------------------------------------------------------- alerts & matches

@app.get("/api/alerts", response_model=Page[AlertOut])
def list_alerts(
    limit: int = Query(50, ge=1, le=200),
    cursor: int | None = Query(None, description="return alerts with id < cursor"),
    object_id: str | None = None,
    max_mag: float | None = Query(None, description="brighter than (smaller magnitude)"),
    min_rb: float | None = Query(None, ge=0, le=1),
    fid: int | None = Query(None, ge=1, le=3),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = select(Alert).order_by(Alert.id.desc()).limit(limit + 1)
    if cursor is not None:
        q = q.where(Alert.id < cursor)
    if object_id:
        q = q.where(Alert.object_id == object_id)
    if max_mag is not None:
        q = q.where(Alert.magpsf <= max_mag)
    if min_rb is not None:
        q = q.where(Alert.rb >= min_rb)
    if fid is not None:
        q = q.where(Alert.fid == fid)
    rows = db.scalars(q).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return Page[AlertOut](items=rows, next_cursor=rows[-1].id if has_more else None)


@app.get("/api/objects/{object_id}/lightcurve", response_model=list[AlertOut])
def light_curve(object_id: str, _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Alert).where(Alert.object_id == object_id).order_by(Alert.jd).limit(1000)).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "object not found")
    return rows


@app.get("/api/matches", response_model=Page[MatchOut])
def list_matches(
    limit: int = Query(50, ge=1, le=200),
    cursor: int | None = None,
    rule_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (select(Match).options(joinedload(Match.alert), joinedload(Match.rule))
         .where(Match.user_id == user.id).order_by(Match.id.desc()).limit(limit + 1))
    if cursor is not None:
        q = q.where(Match.id < cursor)
    if rule_id is not None:
        q = q.where(Match.rule_id == rule_id)
    rows = db.scalars(q).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [MatchOut(id=m.id, rule_id=m.rule_id, rule_name=m.rule.name, created_at=m.created_at,
                      alert=AlertOut.model_validate(m.alert)) for m in rows]
    return Page[MatchOut](items=items, next_cursor=rows[-1].id if has_more else None)


@app.get("/api/stats", response_model=Stats)
def stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        stream_length = bus.get_redis().xlen(bus.ALERT_STREAM)
    except redis.RedisError:
        stream_length = None
    count = lambda q: db.scalar(q) or 0  # noqa: E731
    return Stats(
        total_alerts=count(select(func.max(Alert.id))),  # O(1) estimate; exact COUNT is slow on big tables
        total_matches=count(select(func.max(Match.id))),
        my_rules=count(select(func.count()).select_from(Rule).where(Rule.user_id == user.id)),
        my_matches=count(select(func.count()).select_from(Match).where(Match.user_id == user.id)),
        stream_length=stream_length,
    )


# ---------------------------------------------------------------- websocket

@app.websocket("/ws")
async def live(websocket: WebSocket, token: str = ""):
    """Pushes every new alert (type=alert) and this user's matches (type=match)."""
    try:
        user_id = decode_token(token)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()

    pubsub = bus.get_async_redis().pubsub()
    await pubsub.subscribe(bus.LIVE_CHANNEL, bus.user_channel(user_id))

    async def pump():
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            kind = "alert" if msg["channel"] == bus.LIVE_CHANNEL else "match"
            await websocket.send_text(json.dumps({"type": kind, "data": json.loads(msg["data"])}))

    async def drain():
        while True:  # raises WebSocketDisconnect when the client leaves
            await websocket.receive_text()

    tasks = [asyncio.create_task(pump()), asyncio.create_task(drain())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await pubsub.unsubscribe()
        await pubsub.aclose()
