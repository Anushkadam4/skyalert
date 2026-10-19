"""Rule engine: compiles user rules and matches alerts against them.

Matching every alert against every rule is O(rules) per alert. Most
interesting rules are spatial ("anything near this galaxy"), so we index
cone rules on a sky grid. An alert only needs to be checked against the
rules registered in its own grid cell plus the rules that have no cone.
"""
from __future__ import annotations

import math
import operator
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

ALLOWED_FIELDS = ("magpsf", "sigmapsf", "fid", "rb", "ndethist", "ra", "dec", "jd")
OPS = {
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
    "eq": operator.eq,
    "neq": operator.ne,
}


def angular_sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle distance in degrees (haversine; stable for small angles)."""
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1, dec1, ra2, dec2))
    s = math.sin((dec2 - dec1) / 2) ** 2 + math.cos(dec1) * math.cos(dec2) * math.sin((ra2 - ra1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(s))))


@dataclass(frozen=True)
class Cone:
    ra: float
    dec: float
    radius_deg: float

    def contains(self, ra: float, dec: float) -> bool:
        return angular_sep_deg(self.ra, self.dec, ra, dec) <= self.radius_deg


@dataclass(frozen=True)
class CompiledRule:
    id: int
    user_id: int
    name: str
    conditions: tuple[tuple[str, Any, float], ...]
    cone: Cone | None = None
    webhook_url: str | None = None

    def matches(self, alert: Mapping[str, Any]) -> bool:
        for field, op, value in self.conditions:
            if not op(alert[field], value):
                return False
        if self.cone is not None and not self.cone.contains(alert["ra"], alert["dec"]):
            return False
        return True


def compile_rule(
    rule_id: int,
    user_id: int,
    name: str,
    conditions: Iterable[Mapping[str, Any]],
    cone_ra: float | None = None,
    cone_dec: float | None = None,
    cone_radius_arcsec: float | None = None,
    webhook_url: str | None = None,
) -> CompiledRule:
    compiled = []
    for c in conditions:
        if c["field"] not in ALLOWED_FIELDS:
            raise ValueError(f"unknown field {c['field']!r}")
        if c["op"] not in OPS:
            raise ValueError(f"unknown op {c['op']!r}")
        compiled.append((c["field"], OPS[c["op"]], float(c["value"])))
    cone = None
    if cone_ra is not None and cone_dec is not None and cone_radius_arcsec is not None:
        cone = Cone(cone_ra % 360.0, cone_dec, cone_radius_arcsec / 3600.0)
    return CompiledRule(rule_id, user_id, name, tuple(compiled), cone, webhook_url)


def compile_orm_rule(rule) -> CompiledRule:
    return compile_rule(
        rule.id, rule.user_id, rule.name, rule.conditions or [],
        rule.cone_ra, rule.cone_dec, rule.cone_radius_arcsec, rule.webhook_url,
    )


class RuleIndex:
    """Grid index over the sky for cone rules.

    The sky is split into cells of `cell_deg` x `cell_deg` in (RA, Dec).
    A cone rule is registered in every cell its cone could touch, so a
    lookup by the alert's cell returns a superset of matching cone rules;
    the exact check in CompiledRule.matches removes false positives.
    """

    def __init__(self, rules: Iterable[CompiledRule] = (), cell_deg: float = 1.0):
        self.cell_deg = cell_deg
        self.n_ra = math.ceil(360.0 / cell_deg)
        self.n_dec = math.ceil(180.0 / cell_deg)
        self.global_rules: list[CompiledRule] = []
        self.cells: dict[tuple[int, int], list[CompiledRule]] = defaultdict(list)
        self.size = 0
        for r in rules:
            self.add(r)

    def _dec_idx(self, dec: float) -> int:
        return min(self.n_dec - 1, max(0, int(math.floor((dec + 90.0) / self.cell_deg))))

    def _ra_idx(self, ra: float) -> int:
        return int(math.floor((ra % 360.0) / self.cell_deg)) % self.n_ra

    def _cells_for_cone(self, cone: Cone) -> Iterable[tuple[int, int]]:
        r = cone.radius_deg
        eps = 1e-9
        dec_lo = self._dec_idx(max(-90.0, cone.dec - r - eps))
        dec_hi = self._dec_idx(min(90.0, cone.dec + r + eps))

        # Exact maximum RA half-width of a cone: asin(sin r / cos dec0).
        # If the cone reaches a pole, it covers every RA.
        if abs(cone.dec) + r >= 90.0 - eps:
            ra_cells: Iterable[int] = range(self.n_ra)
        else:
            ratio = math.sin(math.radians(r)) / math.cos(math.radians(cone.dec))
            half_width = 180.0 if ratio >= 1 else math.degrees(math.asin(ratio)) + eps
            if half_width >= 180.0:
                ra_cells = range(self.n_ra)
            else:
                lo = int(math.floor((cone.ra - half_width) / self.cell_deg))
                hi = int(math.floor((cone.ra + half_width) / self.cell_deg))
                ra_cells = {i % self.n_ra for i in range(lo, hi + 1)}

        for j in range(dec_lo, dec_hi + 1):
            for i in ra_cells:
                yield (i, j)

    def add(self, rule: CompiledRule) -> None:
        self.size += 1
        if rule.cone is None:
            self.global_rules.append(rule)
            return
        for cell in self._cells_for_cone(rule.cone):
            self.cells[cell].append(rule)

    def candidates(self, alert: Mapping[str, Any]) -> Iterable[CompiledRule]:
        yield from self.global_rules
        yield from self.cells.get((self._ra_idx(alert["ra"]), self._dec_idx(alert["dec"])), ())

    def match(self, alert: Mapping[str, Any]) -> list[CompiledRule]:
        return [r for r in self.candidates(alert) if r.matches(alert)]
