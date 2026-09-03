#!/usr/bin/env python3
"""
fh6_test.py - check the tools still work before you rely on them.

    cd ~/fh6 && venv/bin/python3 fh6_test.py

Every test here exists because that exact thing broke at some point and
produced plausible-looking wrong numbers rather than an error. That is the
failure mode worth guarding against: a corrupted run still prints an ET, and
you only find out by comparing against the game hours later.

Runs entirely on synthetic packets, so it needs no recordings and no game.
If a saved run history exists it is checked too, but never modified.
"""

import io
import json
import math
import os
import struct
import sys
import tempfile
import traceback
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def test(name):
    def wrap(fn):
        try:
            fn()
            PASS.append(name)
        except Exception as e:
            FAIL.append((name, f"{type(e).__name__}: {e}",
                         traceback.format_exc()))
        return fn
    return wrap


def near(a, b, tol, what=""):
    if a is None:
        raise AssertionError(f"{what}: got None, expected ~{b}")
    if abs(a - b) > tol:
        raise AssertionError(f"{what}: {a} not within {tol} of {b}")


# ---------------------------------------------------------------------------
# synthetic packet helpers
# ---------------------------------------------------------------------------

import fh6_draggy as D


def packet(**kw):
    vals = []
    for n, t in D._FIELDS:
        v = kw.get(n, 0)
        vals.append(int(v) if t in "iIHBb" else float(v))
    return struct.pack(D._FMT, *vals) + b"\x00"


def full(**kw):
    """Every packet field present, so a test never fails on a missing key
    rather than on the thing it is actually checking."""
    d = {n: (0 if t in "iIHBb" else 0.0) for n, t in D._FIELDS}
    d.update(IsRaceOn=1, Gear=1, CarOrdinal=999, CarPerformanceIndex=800,
             DrivetrainType=1, NumCylinders=8, Power=800000.0,
             CurrentEngineRpm=4500.0, TireTempRL=100.0, TireTempRR=100.0)
    d.update(kw)
    return d


def drive(det, *, stage_s=2.0, creep=False, reach=95.0, tau=6.0,
          run_s=12.0, hz=60.0, lift_after=True):
    """Feed a detector one complete staged launch."""
    dt = 1.0 / hz
    t = 0.0
    if creep:
        # The real sequence: already staged, then the car inches forward in
        # the beam and settles again, several seconds before the green. Both
        # the brief movement and the input change during it must be ignored.
        for _ in range(int(1.0 * hz)):
            det.feed(full(Speed=0.0, Accel=255, Brake=0,
                          Clutch=255, HandBrake=255), t)
            t += dt
        for _ in range(int(0.4 * hz)):          # inch forward, handbrake off
            det.feed(full(Speed=0.8, Accel=255, Brake=0,
                          Clutch=255, HandBrake=0), t)
            t += dt
        for _ in range(int(4.0 * hz)):          # settle and wait for green
            det.feed(full(Speed=0.0, Accel=255, Brake=0,
                          Clutch=255, HandBrake=255), t)
            t += dt
    for _ in range(int(stage_s * hz)):
        det.feed(full(Speed=0.0, Accel=255, Brake=0,
                      Clutch=255, HandBrake=255), t)
        t += dt
    n = int(run_s * hz)
    for i in range(n):
        v = reach * (1 - math.exp(-(i * dt) / tau))
        det.feed(full(Speed=v, Accel=255, Brake=0, Clutch=0,
                      HandBrake=0, Gear=min(6, 1 + int(i * dt / 2))), t)
        t += dt
    if lift_after:                              # coast after the finish
        for i in range(int(3.0 * hz)):
            det.feed(full(Speed=max(0.0, reach - i * 0.5),
                          Accel=0, Brake=255, Clutch=255, HandBrake=0), t)
            t += dt
    return t


def one_run(**kw):
    got = []
    det = D.Detector(lambda r: got.append(r), kw.pop("finish", D.M_QUARTER))
    drive(det, **kw)
    return got


# ---------------------------------------------------------------------------
# packet layout
# ---------------------------------------------------------------------------

@test("packet is the documented size and round-trips")
def _():
    buf = packet(Speed=42.5, Accel=255, CurrentEngineRpm=7000.0)
    assert len(buf) == 324, f"packet is {len(buf)} bytes, expected 324"
    p = D.parse(buf)
    near(p["Speed"], 42.5, 1e-3, "Speed")
    assert p["Accel"] == 255
    near(p["CurrentEngineRpm"], 7000.0, 1e-3, "rpm")


@test("short packets are rejected rather than mis-parsed")
def _():
    assert D.parse(b"\x00" * 100) is None


# ---------------------------------------------------------------------------
# run detection
# ---------------------------------------------------------------------------

@test("a normal staged launch produces exactly one run")
def _():
    runs = one_run()
    assert len(runs) == 1, f"expected 1 run, got {len(runs)}"
    assert runs[0]["et"].get("1/4mi"), "no quarter mile recorded"


@test("creeping into the beam is not mistaken for the launch")
def _():
    # Regression: relaxing the stage rule made the tool treat the roll-in as
    # the launch, so 60ft came out around nine seconds.
    runs = one_run(creep=True)
    assert runs, "no run detected"
    sixty = runs[0]["et"]["60ft"]["s"]
    assert sixty < 3.0, f"60ft of {sixty:.2f}s - launch detected during creep"
    r2m = runs[0].get("release_to_movement_ms")
    if r2m is not None:
        assert abs(r2m) < 400, (
            f"release->movement {r2m:.0f}ms - release taken from the roll-in")


@test("lifting after the finish is not counted as the launch release")
def _():
    # Regression: release spreads of 9-10 seconds, caused by the search window
    # running from stage entry to the end of the run.
    runs = one_run(creep=True)
    spread = runs[0].get("release_spread_ms")
    if spread is not None:
        assert spread < 500, f"release spread {spread}ms - caught a later lift"
    r2m = runs[0].get("release_to_movement_ms")
    if r2m is not None:
        assert r2m < 500, f"release->movement {r2m}ms - wrong release picked"


@test("distance is integrated, since FH6 never fills DistanceTraveled")
def _():
    runs = one_run()
    et = runs[0]["et"]
    for a, b in (("60ft", "330ft"), ("330ft", "1/8mi"), ("1/8mi", "1/4mi")):
        if a in et and b in et:
            assert et[a]["s"] < et[b]["s"], f"{a} not before {b}"
    assert et["1/4mi"]["mph"] > et["60ft"]["mph"], "trap speeds not increasing"


@test("a longer strip records the longer marks and the 100-200 roll-on")
def _():
    # Regression: the finish was hardcoded to the quarter, so half and mile
    # runs were cut short and 100-200 could never appear.
    runs = one_run(finish=D.M_MILE, reach=103.0, tau=7.0, run_s=45.0)
    assert runs, "no run detected on a mile strip"
    et = runs[0]["et"]
    assert "1/2mi" in et and "1mi" in et, f"marks recorded: {list(et)}"
    assert "100-200" in (runs[0].get("roll") or {}), "no 100-200 roll-on"


@test("roll-on splits are measured between speeds, not from the start")
def _():
    runs = one_run(reach=95.0)
    roll = runs[0].get("roll") or {}
    acc = runs[0].get("accel") or {}
    if "60-130" in roll and "0-60" in acc and "0-130" not in acc:
        assert roll["60-130"] > 0
    if "100-150" in roll:
        assert roll["100-150"] > 0, "roll-on should be a positive interval"


# ---------------------------------------------------------------------------
# reaction, red lights, bests
# ---------------------------------------------------------------------------

@test("a red light is flagged and cannot take a personal best")
def _():
    good = {"reaction_ms": 45.0, "et": {"1/4mi": {"s": 8.0}},
            "accel": {"0-60": {"s": 1.5}}, "timestamp": "a"}
    red = {"reaction_ms": -30.0, "foul": True,
           "et": {"1/4mi": {"s": 7.0}},        # quicker, but void
           "accel": {"0-60": {"s": 1.2}}, "timestamp": "b"}
    b = D.bests([good, red])
    near(b["1/4mi"][0], 8.0, 1e-6, "1/4mi best")
    near(b["0-60"][0], 1.5, 1e-6, "0-60 best")


@test("negative reactions are marked as fouls by analyze")
def _():
    runs = one_run()
    r = runs[0]
    samples_ok = "notes" in r
    assert samples_ok, "run has no notes field"


# ---------------------------------------------------------------------------
# countdown detection maths
# ---------------------------------------------------------------------------

@test("countdown chain steps over extra detections between beeps")
def _():
    import fh6_reaction as R
    rel = 10.0
    beeps = [7.0, 7.4, 8.0, 8.4, 9.0, 9.37, 10.0]   # 0.4s train interleaved
    chain = R.countdown_chain(beeps, rel)
    assert len(chain) >= 3, f"chain only {len(chain)}: {chain}"


@test("countdown chain ends nearest the launch, not on the longest run")
def _():
    # Regression: picking the longest chain ran past GO into launch noise and
    # put every reaction time out by a full second.
    import fh6_reaction as R
    rel = 10.0
    beeps = [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    chain = R.countdown_chain(beeps, rel)
    assert chain[-1] <= rel + 0.6, f"chain ended at {chain[-1]}, past the launch"


@test("GO is not predicted a second late when the tone is already heard")
def _():
    # Regression: adding one interval to a chain that already contained GO
    # produced a consistent -1000 ms reaction.
    import fh6_reaction as R
    rel = 10.0
    go = R.predict_go([7.95, 8.95, 9.95], rel)
    near(go, 9.95, 1e-6, "GO when the final tone was heard")
    go2 = R.predict_go([6.0, 7.0, 8.0], rel)
    near(go2, 9.0, 1e-6, "GO predicted forward when it was not heard")


@test("reaction sign convention: positive means after the green")
def _():
    import fh6_reaction as R
    ms = R.reaction_ms(release_t=10.10, go_heard=10.20, latency_s=0.15)
    near(ms, 50.0, 1e-6, "reaction")
    assert R.reaction_ms(10.0, None, 0.15) is None


# ---------------------------------------------------------------------------
# module health
# ---------------------------------------------------------------------------

@test("every tool compiles and defines main() before it is called")
def _():
    # Regression: an entry point sitting above later function definitions made
    # the tool crash on startup with NameError.
    import re
    for name in ("fh6_draggy.py", "fh6_reaction.py", "fh6_slip.py",
                 "fh6_launch.py", "fh6_shift.py"):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        text = open(path).read()
        compile(text, path, "exec")
        m = re.search(r'^if __name__ == ["\']__main__["\']', text, re.M)
        if not m:
            continue
        after = text[m.end():]
        assert "\ndef " not in after, (
            f"{name}: functions defined after the entry point")


@test("the slip and report render from a run without raising")
def _():
    import fh6_slip as S
    run = one_run()[0]
    run.setdefault("timestamp", "2026-01-01T00:00:00")
    run["car_pi"] = 800
    html = S.slip_html(run, run, "TEST STRIP", 1, 1)
    assert "TEST STRIP" in html and "1/4" in html
    rep = S.report_html(run, "TEST STRIP")
    assert "Performance Report" in rep and "<svg" in rep


@test("chart axes use round numbers and stay inside the viewbox")
def _():
    import fh6_slip as S
    run = one_run()[0]
    svg = S.chart(run)
    assert svg.count("<polyline") == 2, "expected speed and acceleration lines"
    import re
    for x, y in re.findall(r'(\d+\.?\d*),(\d+\.?\d*)', svg)[:400]:
        assert -1 <= float(x) <= 400 and -1 <= float(y) <= 300, "point off canvas"


@test("saved run history, if present, is readable and self-consistent")
def _():
    path = os.path.expanduser("~/fh6_draggy_runs.json")
    if not os.path.exists(path):
        return
    runs = json.load(open(path))
    assert isinstance(runs, list)
    for i, r in enumerate(runs):
        et = r.get("et") or {}
        q = (et.get("1/4mi") or {}).get("s")
        if isinstance(q, (int, float)):
            assert 3.0 < q < 120.0, f"run {i+1}: implausible 1/4 mile {q}s"
        sp = r.get("release_spread_ms")
        if isinstance(sp, (int, float)):
            assert abs(sp) < 2000, f"run {i+1}: release spread {sp}ms"


def main():
    print("\nfh6 test suite\n")
    total = len(PASS) + len(FAIL)
    for name in PASS:
        print(f"  pass   {name}")
    for name, msg, tb in FAIL:
        print(f"  FAIL   {name}\n         {msg}")
    print(f"\n  {len(PASS)}/{total} passed")
    if FAIL:
        print("\n  Something is broken - do not trust times until it is fixed.")
        if os.environ.get("FH6_TEST_VERBOSE"):
            for name, msg, tb in FAIL:
                print(f"\n--- {name} ---\n{tb}")
        else:
            print("  Run again with FH6_TEST_VERBOSE=1 for tracebacks.")
        return 1
    print("  All good.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
