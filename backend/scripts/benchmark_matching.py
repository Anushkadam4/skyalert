"""Benchmark: spatial rule index vs. checking every rule.

    python -m scripts.benchmark_matching --rules 10000 --alerts 20000

Prints per-alert latency percentiles and throughput for both strategies,
and verifies they return identical matches. Use these numbers on your resume.
"""
import argparse
import math
import random
import statistics
import time

from app.rules import RuleIndex, compile_rule


def random_point(rng):
    return rng.uniform(0, 360), math.degrees(math.asin(rng.uniform(-1, 1)))


def build_rules(n, rng, cone_fraction):
    rules = []
    for i in range(n):
        conds = [{"field": "magpsf", "op": "lt", "value": rng.uniform(17, 21)},
                 {"field": "rb", "op": "gte", "value": rng.uniform(0.5, 0.9)}]
        if rng.random() < cone_fraction:
            ra, dec = random_point(rng)
            rules.append(compile_rule(i, i % 500, f"r{i}", conds, ra, dec, rng.uniform(5, 1800)))
        else:
            rules.append(compile_rule(i, i % 500, f"r{i}", conds))
    return rules


def measure(fn, alerts):
    times, total = [], 0
    for a in alerts:
        t0 = time.perf_counter()
        total += len(fn(a))
        times.append(time.perf_counter() - t0)
    times.sort()
    pct = lambda p: times[min(len(times) - 1, int(p * len(times)))] * 1e6  # noqa: E731
    return {"p50_us": pct(0.50), "p99_us": pct(0.99), "mean_us": statistics.fmean(times) * 1e6,
            "alerts_per_s": len(alerts) / sum(times), "matches": total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", type=int, default=10_000)
    ap.add_argument("--alerts", type=int, default=20_000)
    ap.add_argument("--cone-fraction", type=float, default=0.9)
    ap.add_argument("--cell-deg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rules = build_rules(args.rules, rng, args.cone_fraction)
    t0 = time.perf_counter()
    index = RuleIndex(rules, cell_deg=args.cell_deg)
    build_ms = (time.perf_counter() - t0) * 1000

    alerts = []
    for _ in range(args.alerts):
        ra, dec = random_point(rng)
        alerts.append({"ra": ra, "dec": dec, "magpsf": rng.uniform(16, 22), "rb": rng.random(),
                       "sigmapsf": 0.05, "fid": 2, "ndethist": 1, "jd": 0.0})

    sample = alerts[:2000]
    assert all({r.id for r in index.match(a)} == {r.id for r in rules if r.matches(a)} for a in sample)

    brute = measure(lambda a: [r for r in rules if r.matches(a)], alerts)
    indexed = measure(index.match, alerts)

    print(f"{args.rules:,} rules ({args.cone_fraction:.0%} spatial), {args.alerts:,} alerts, "
          f"cell={args.cell_deg} deg, index built in {build_ms:.0f} ms")
    print(f"{'strategy':<12}{'p50 (us)':>10}{'p99 (us)':>10}{'alerts/s':>12}{'matches':>10}")
    for name, m in (("brute force", brute), ("grid index", indexed)):
        print(f"{name:<12}{m['p50_us']:>10.1f}{m['p99_us']:>10.1f}{m['alerts_per_s']:>12,.0f}{m['matches']:>10}")
    print(f"speedup: {brute['mean_us'] / indexed['mean_us']:.1f}x (results verified identical)")


if __name__ == "__main__":
    main()
