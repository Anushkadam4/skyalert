import math
import random

import pytest

from app.rules import RuleIndex, angular_sep_deg, compile_rule


def alert(**kw):
    base = dict(ra=10.0, dec=20.0, magpsf=18.5, sigmapsf=0.05, fid=2, rb=0.9, ndethist=3, jd=2461330.5)
    return {**base, **kw}


def test_angular_separation_known_values():
    assert angular_sep_deg(0, 0, 0, 0) == 0
    assert angular_sep_deg(0, 0, 90, 0) == pytest.approx(90)
    assert angular_sep_deg(0, 89, 180, 89) == pytest.approx(2)  # across the pole
    assert angular_sep_deg(359.9, 0, 0.1, 0) == pytest.approx(0.2)  # across RA = 0


def test_conditions_all_must_hold():
    rule = compile_rule(1, 1, "bright & real", [
        {"field": "magpsf", "op": "lt", "value": 19},
        {"field": "rb", "op": "gte", "value": 0.8},
    ])
    assert rule.matches(alert())
    assert not rule.matches(alert(magpsf=19.5))
    assert not rule.matches(alert(rb=0.5))


def test_cone_rule():
    rule = compile_rule(1, 1, "near target", [], cone_ra=10.0, cone_dec=20.0, cone_radius_arcsec=60)
    assert rule.matches(alert(ra=10.0, dec=20.0 + 50 / 3600))
    assert not rule.matches(alert(ra=10.0, dec=20.0 + 70 / 3600))


def test_unknown_field_rejected():
    with pytest.raises(ValueError):
        compile_rule(1, 1, "x", [{"field": "__class__", "op": "eq", "value": 1}])


def random_point(rng):
    return rng.uniform(0, 360), math.degrees(math.asin(rng.uniform(-1, 1)))


@pytest.mark.parametrize("cell_deg", [0.5, 1.0, 5.0])
def test_index_agrees_with_brute_force(cell_deg):
    """The index must return exactly what checking every rule would return,
    including cones that straddle RA=0/360 and cones around the poles."""
    rng = random.Random(42)
    rules = []
    for i in range(400):
        if i % 4 == 0:
            rules.append(compile_rule(i, 1, f"r{i}", [{"field": "magpsf", "op": "lt", "value": rng.uniform(17, 21)}]))
        else:
            ra, dec = random_point(rng)
            if i % 10 == 1:
                ra = rng.choice([0.01, 359.99])  # RA wrap-around
            if i % 10 == 2:
                dec = rng.choice([89.5, -89.5])  # near a pole
            rules.append(compile_rule(i, 1, f"r{i}", [], ra, dec, rng.uniform(1, 4 * 3600)))
    index = RuleIndex(rules, cell_deg=cell_deg)

    checked = 0
    for _ in range(4000):
        ra, dec = random_point(rng)
        # bias half the alerts to land near rule centres so matches actually happen
        if rng.random() < 0.5:
            target = rng.choice([r for r in rules if r.cone])
            ra = (target.cone.ra + rng.uniform(-3, 3)) % 360
            dec = max(-90, min(90, target.cone.dec + rng.uniform(-3, 3)))
        a = alert(ra=ra, dec=dec, magpsf=rng.uniform(16, 22))
        expected = {r.id for r in rules if r.matches(a)}
        assert {r.id for r in index.match(a)} == expected
        checked += len(expected)
    assert checked > 1000  # sanity: the test exercised real matches
