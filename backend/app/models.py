"""SQLAlchemy ORM models."""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rules: Mapped[list["Rule"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Rule(Base):
    """A user-defined filter. An alert matches when every condition holds
    and (if a cone is set) the alert lies inside the cone."""

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    cone_ra: Mapped[float | None] = mapped_column(Float, nullable=True)
    cone_dec: Mapped[float | None] = mapped_column(Float, nullable=True)
    cone_radius_arcsec: Mapped[float | None] = mapped_column(Float, nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="rules")


class Alert(Base):
    """One detection from the survey alert stream (fields follow ZTF naming)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candid: Mapped[int] = mapped_column(BigInteger, unique=True)  # survey's unique candidate id
    object_id: Mapped[str] = mapped_column(String(32), index=True)
    ra: Mapped[float] = mapped_column(Float)
    dec: Mapped[float] = mapped_column(Float)
    magpsf: Mapped[float] = mapped_column(Float)
    sigmapsf: Mapped[float] = mapped_column(Float)
    fid: Mapped[int] = mapped_column(Integer)  # 1=g, 2=r, 3=i
    rb: Mapped[float] = mapped_column(Float)  # real/bogus score, 0..1
    ndethist: Mapped[int] = mapped_column(Integer)
    jd: Mapped[float] = mapped_column(Float, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("rule_id", "alert_id", name="uq_match_rule_alert"),
        Index("ix_matches_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"))
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    alert: Mapped[Alert] = relationship()
    rule: Mapped[Rule] = relationship()
