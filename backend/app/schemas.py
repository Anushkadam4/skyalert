"""Pydantic request/response models."""
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .rules import ALLOWED_FIELDS, OPS

FieldName = Literal[ALLOWED_FIELDS]  # type: ignore[valid-type]
OpName = Literal[tuple(OPS)]  # type: ignore[valid-type]


class Credentials(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    password: str = Field(min_length=8, max_length=128)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    created_at: datetime


class Condition(BaseModel):
    field: FieldName
    op: OpName
    value: float


class ConeIn(BaseModel):
    ra: float = Field(ge=0, lt=360)
    dec: float = Field(ge=-90, le=90)
    radius_arcsec: float = Field(gt=0, le=36000)  # up to 10 degrees


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    conditions: list[Condition] = Field(default_factory=list, max_length=20)
    cone: ConeIn | None = None
    webhook_url: str | None = Field(default=None, max_length=500)
    active: bool = True

    @field_validator("webhook_url")
    @classmethod
    def _http_only(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("webhook_url must start with http:// or https://")
        return v


class RuleOut(BaseModel):
    id: int
    name: str
    conditions: list[Condition]
    cone: ConeIn | None
    webhook_url: str | None
    active: bool
    created_at: datetime

    @classmethod
    def from_orm_rule(cls, r) -> "RuleOut":
        cone = None
        if r.cone_ra is not None:
            cone = ConeIn(ra=r.cone_ra, dec=r.cone_dec, radius_arcsec=r.cone_radius_arcsec)
        return cls(
            id=r.id, name=r.name, conditions=r.conditions or [], cone=cone,
            webhook_url=r.webhook_url, active=r.active, created_at=r.created_at,
        )


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    candid: int
    object_id: str
    ra: float
    dec: float
    magpsf: float
    sigmapsf: float
    fid: int
    rb: float
    ndethist: int
    jd: float
    received_at: datetime


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rule_id: int
    rule_name: str
    created_at: datetime
    alert: AlertOut


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: int | None


class Stats(BaseModel):
    total_alerts: int
    total_matches: int
    my_rules: int
    my_matches: int
    stream_length: int | None
