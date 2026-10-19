"""Alert producer: simulates a ZTF-like alert stream and pushes it into Redis.

Real surveys (ZTF today, Rubin/LSST next) publish alerts through brokers
such as Fink, ALeRCE, ANTARES or Lasair. This simulator writes alerts in
the same shape so the rest of the system doesn't care where they came
from: to use a real feed, write a small adapter that maps each incoming
alert to the same JSON fields and XADDs it to `bus.ALERT_STREAM`.

Simulated population:
  * supernova-like transients: rise over ~2 weeks, then fade
  * periodic variable stars
  * bogus detections (artefacts) with low real/bogus scores
Some objects are placed near well-known galaxies (M31, M101, NGC 4993) so
cone-search rules have something to find.
"""
import argparse
import json
import logging
import math
import random
import string
import time
from dataclasses import dataclass

from . import bus

log = logging.getLogger("skyalert.producer")

HOTSPOTS = [(10.6847, 41.2690), (210.8023, 54.3489), (197.4487, -23.3839)]  # M31, M101, NGC 4993
JD_START = 2461330.5  # 2026-10-17


@dataclass
class SkyObject:
    object_id: str
    kind: str
    ra: float
    dec: float
    base_mag: float
    t0: float
    period: float = 0.0
    ndet: int = 0

    def magnitude(self, jd: float) -> float:
        dt = jd - self.t0
        if self.kind == "sn":
            # rise ~2.5 mag in 15 days, then fade ~0.03 mag/day
            return self.base_mag - (2.5 * dt / 15 if dt < 15 else 2.5 - 0.03 * (dt - 15))
        if self.kind == "variable":
            return self.base_mag + 0.8 * math.sin(2 * math.pi * dt / self.period)
        return self.base_mag + random.gauss(0, 0.5)


def random_object_id(year: int = 26) -> str:
    return f"ZTF{year}" + "".join(random.choices(string.ascii_lowercase, k=7))


def random_sky_position() -> tuple[float, float]:
    """Uniform on the sphere (not uniform in dec!), limited to dec > -30 like ZTF."""
    ra = random.uniform(0, 360)
    while True:
        dec = math.degrees(math.asin(random.uniform(-1, 1)))
        if dec > -30:
            return ra, dec


def make_population(n: int, jd: float) -> list[SkyObject]:
    objs = []
    for _ in range(n):
        if random.random() < 0.05:  # cluster some objects around famous galaxies
            hra, hdec = random.choice(HOTSPOTS)
            ra = (hra + random.gauss(0, 0.05) / max(0.1, math.cos(math.radians(hdec)))) % 360
            dec = max(-90.0, min(90.0, hdec + random.gauss(0, 0.05)))
        else:
            ra, dec = random_sky_position()
        kind = random.choices(["sn", "variable", "bogus"], weights=[0.25, 0.5, 0.25])[0]
        objs.append(SkyObject(
            object_id=random_object_id(), kind=kind, ra=ra, dec=dec,
            base_mag=random.uniform(17.5, 21.0), t0=jd - random.uniform(0, 30),
            period=random.uniform(0.3, 40.0),
        ))
    return objs


def make_alert(obj: SkyObject, jd: float, candid: int) -> dict:
    obj.ndet += 1
    mag = min(23.0, max(12.0, obj.magnitude(jd)))
    sigma = 0.02 + 0.05 * math.exp((mag - 19) / 1.5)
    rb = random.uniform(0.0, 0.4) if obj.kind == "bogus" else random.uniform(0.6, 1.0)
    # per-detection astrometric scatter of ~0.3 arcsec
    jitter = 0.3 / 3600
    return {
        "candid": candid,
        "object_id": obj.object_id,
        "ra": round((obj.ra + random.gauss(0, jitter)) % 360, 7),
        "dec": round(max(-90.0, min(90.0, obj.dec + random.gauss(0, jitter))), 7),
        "magpsf": round(mag + random.gauss(0, sigma), 3),
        "sigmapsf": round(sigma, 3),
        "fid": random.choice([1, 2, 2, 3]),
        "rb": round(rb, 3),
        "ndethist": obj.ndet,
        "jd": round(jd, 6),
    }


def run(rate: float, total: int | None, n_objects: int, seed: int | None, sim_seconds: float = 120.0) -> None:
    if seed is not None:
        random.seed(seed)
    r = bus.get_redis()
    jd = JD_START
    population = make_population(n_objects, jd)
    candid = int(time.time() * 1000) * 1000  # unique and increasing across restarts
    sent, batch = 0, max(1, int(rate // 10))
    interval = batch / rate
    log.info("producing %.0f alerts/s from %d objects", rate, n_objects)
    while total is None or sent < total:
        t0 = time.monotonic()
        pipe = r.pipeline(transaction=False)
        for _ in range(batch):
            jd += sim_seconds / 86400  # simulated clock runs fast so light curves evolve visibly
            candid += 1
            pipe.xadd(bus.ALERT_STREAM, {"data": json.dumps(make_alert(random.choice(population), jd, candid))},
                      maxlen=bus.STREAM_MAXLEN, approximate=True)
            sent += 1
            if total is not None and sent >= total:
                break
        pipe.execute()
        if random.random() < 0.01:  # new transients keep appearing
            population.extend(make_population(10, jd))
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))
    log.info("done: sent %d alerts", sent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated ZTF-like alert producer")
    parser.add_argument("--rate", type=float, default=20.0, help="alerts per second")
    parser.add_argument("--total", type=int, default=None, help="stop after N alerts")
    parser.add_argument("--objects", type=int, default=1500)
    parser.add_argument("--sim-seconds", type=float, default=120.0,
                        help="simulated time that passes per alert")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run(args.rate, args.total, args.objects, args.seed, args.sim_seconds)


if __name__ == "__main__":
    main()
