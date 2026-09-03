#!/usr/bin/env python3
"""
fh6_shift.py - work out where this car actually wants to be shifted.

    python3 fh6_shift.py fh6_raw.csv

Feed it a raw telemetry dump (fh6_draggy.py --raw) containing at least one
wide-open pull through the gears. Nothing needs to be known about the car in
advance; the gearing and the power curve are both measured from the drive.

THE IDEA
    Force at the wheels is power divided by road speed, and road speed does not
    change across a shift. So the right gear at any instant is simply whichever
    one puts the engine where it makes more POWER - not more torque, and not
    necessarily at the rev limiter.

    Shifting drops the engine to rpm_next = rpm_now * (ratio_next / ratio_now).
    The optimal shift point is the rpm where power at rpm_next first exceeds
    power at rpm_now. Hold past that and the next gear would have been pulling
    harder the whole time; shift before it and you gave up power you still had.

    Gear ratios are not read from anywhere - rpm/speed in each gear measures
    the whole chain (gearbox, final drive, tyre size) in one number, which is
    all the ratio comparison needs.
"""

import argparse
import csv
import sys

import numpy as np

W_TO_HP = 1 / 745.7


def load(path):
    try:
        rows = list(csv.DictReader(open(path)))
    except OSError as e:
        sys.exit(f"cannot read {path}: {e}")
    need = {"Accel", "Speed", "Power", "CurrentEngineRpm", "Gear"}
    if not rows or not need.issubset(set(rows[0])):
        sys.exit(f"{path} needs columns: {', '.join(sorted(need))}")
    out = []
    for r in rows:
        try:
            out.append({k: float(v) for k, v in r.items()})
        except (TypeError, ValueError):
            continue
    return out


def gear_ratios(wot):
    """rpm per m/s for each gear, plus how tight the estimate is."""
    ratios = {}
    for g in sorted({int(r["Gear"]) for r in wot}):
        if g == SHIFTING:
            continue
        k = [r["CurrentEngineRpm"] / r["Speed"]
             for r in wot if int(r["Gear"]) == g and r["Speed"] > 4]
        if len(k) < 30:
            continue
        k = np.array(k)
        med = float(np.median(k))
        # discard wheelspin and clutch slip, which inflate rpm for a given speed
        keep = k[np.abs(k - med) < 0.06 * med]
        if len(keep) < 20:
            continue
        ratios[g] = (float(np.median(keep)), float(np.std(keep) / np.median(keep)))
    return ratios


def power_curve(wot, ratios, step=100):
    """
    Median power in each rpm bin, from gears with reliable traction.

    Low gears are excluded: wheelspin means the engine is not actually loaded,
    so those samples describe the tyres rather than the engine.
    """
    good = [g for g in ratios if g >= 3] or list(ratios)
    pts = [(r["CurrentEngineRpm"], r["Power"])
           for r in wot if int(r["Gear"]) in good and r["Power"] > 0]
    if len(pts) < 100:
        pts = [(r["CurrentEngineRpm"], r["Power"]) for r in wot if r["Power"] > 0]
    rpm = np.array([p[0] for p in pts])
    pw = np.array([p[1] for p in pts])
    lo, hi = rpm.min(), rpm.max()
    edges = np.arange(lo // step * step, hi + step, step)
    xs, ys = [], []
    for a, b in zip(edges, edges[1:]):
        m = (rpm >= a) & (rpm < b)
        if m.sum() >= 5:
            xs.append((a + b) / 2)
            ys.append(float(np.median(pw[m])))
    return np.array(xs), np.array(ys)


def shift_points(xs, ys, ratios):
    """For each upshift, the rpm where the next gear starts pulling harder."""
    P = lambda r: float(np.interp(r, xs, ys, left=ys[0], right=ys[-1]))
    gears = sorted(ratios)
    redline = float(xs.max())
    out = []
    for g, nxt in zip(gears, gears[1:]):
        if nxt != g + 1:
            continue
        drop = ratios[nxt][0] / ratios[g][0]
        best = None
        for r in np.arange(xs.min() + 200, redline + 1, 10):
            if P(r * drop) >= P(r):
                best = float(r)
                break
        out.append({
            "from": g, "to": nxt,
            "rpm": best if best is not None else redline,
            "at_limiter": best is None,
            "lands_at": (best if best is not None else redline) * drop,
            "drop_pct": (1 - drop) * 100,
        })
    return out, redline


SHIFTING = 11          # FH6 reports gear 11 for one frame mid-shift


def drop_transient(rows):
    """Remove the mid-shift frames so gear changes read as N -> N+1."""
    return [r for r in rows if r["Gear"] != SHIFTING]


def actual_shifts(rows, lookback=12):
    """
    What the driver did: rpm at each upshift, by gear.

    Throttle is not required at the instant of the shift - anyone using a
    clutch lifts to change gear, so demanding wide-open throttle on that exact
    frame finds nothing. Instead it checks the car was accelerating hard in the
    moments before, and takes the peak rpm reached in that window, which is the
    rpm the shift was actually made at.
    """
    by = {}
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if b["Gear"] != a["Gear"] + 1 or a["Speed"] < 5:
            continue
        win = rows[max(0, i - lookback):i]
        if not win or max(r["Accel"] for r in win) < 250:
            continue
        by.setdefault(int(a["Gear"]), []).append(max(r["CurrentEngineRpm"] for r in win))
    return by


def main():
    ap = argparse.ArgumentParser(description="optimal shift points from FH6 telemetry")
    ap.add_argument("csv", help="raw telemetry dump")
    ap.add_argument("--hp", action="store_true", help="show power in hp not kW")
    a = ap.parse_args()

    rows = drop_transient(load(a.csv))
    wot = [r for r in rows
           if r["Accel"] >= 250 and r["Speed"] > 2
           and r["Power"] > 0 and r["CurrentEngineRpm"] > 1000]
    if len(wot) < 200:
        sys.exit("not enough wide-open-throttle data - do a full pull through the gears")

    ratios = gear_ratios(wot)
    if len(ratios) < 2:
        sys.exit("could not measure gear ratios - need a pull through several gears")
    xs, ys = power_curve(wot, ratios)
    shifts, redline = shift_points(xs, ys, ratios)
    did = actual_shifts(rows)

    unit, scale = ("hp", W_TO_HP) if a.hp else ("kW", 1 / 1000)
    peak_i = int(np.argmax(ys))
    print(f"\n  {len(wot)} wide-open samples, gears {min(ratios)}-{max(ratios)}")
    print(f"  peak power {ys[peak_i]*scale:.0f} {unit} @ {xs[peak_i]:.0f} rpm"
          f"   |   data to {redline:.0f} rpm\n")

    print("  POWER CURVE")
    top = ys.max()
    for x, y in zip(xs, ys):
        if x < xs[peak_i] - 2500:
            continue
        bar = "#" * int(round(38 * y / top))
        mark = "  <- peak" if x == xs[peak_i] else ""
        print(f"   {x:>5.0f} {y*scale:>6.0f} {unit} |{bar}{mark}")

    print("\n  SHIFT POINTS")
    print(f"   {'shift':<8}{'at rpm':>9}{'lands at':>10}{'you used':>11}{'verdict':>26}")
    for s in shifts:
        used = did.get(s["from"])
        u = f"{np.mean(used):.0f}" if used else "-"
        if used:
            diff = np.mean(used) - s["rpm"]
            if abs(diff) < 150:
                v = "about right"
            elif diff > 0:
                v = f"shifting {diff:.0f} rpm too late"
            else:
                v = f"shifting {-diff:.0f} rpm too early"
        else:
            v = ""
        note = " (hold to limiter)" if s["at_limiter"] else ""
        print(f"   {s['from']}->{s['to']:<6}{s['rpm']:>9.0f}{s['lands_at']:>10.0f}"
              f"{u:>11}{v:>26}{note}")

    print("\n  Shift where the next gear starts making more power than this one.")
    if any(s["at_limiter"] for s in shifts):
        print("  Gears marked 'hold to limiter' never cross over - power is still")
        print("  climbing at the top, so there is nothing to gain by shifting early.")
    print()


if __name__ == "__main__":
    main()
