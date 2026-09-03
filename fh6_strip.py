#!/usr/bin/env python3
"""
fh6_strip.py - work out where the finish line actually is.

    python3 fh6_strip.py fh6_check.csv 8.575 8.292 8.208

Give it a raw telemetry dump and the elapsed times the game printed on screen,
in the order the runs happened. For each run it finds how far the car had
travelled at the moment the game's clock said the run was over, and reports
the distance.

WHY
    Our ET assumes the strip is exactly a quarter mile and that the clock
    starts when the car first moves. Both assumptions could be wrong, and they
    fail differently: a wrong distance scales with the run, a wrong start
    reference is a flat offset. Measuring the distance at the game's own time
    tells them apart.

    If the distances come out consistent but not 402.3 m, the strip is a
    different length and the fix is one number. If they scatter, the clocks
    start in different places and no distance will reconcile them.

Distance comes from world position when the dump has it, which is exact,
rather than from integrating speed.
"""

import csv
import math
import sys


def load(path):
    try:
        rows = list(csv.DictReader(open(path)))
    except OSError as e:
        sys.exit(f"cannot read {path}: {e}")
    out = []
    for r in rows:
        try:
            out.append({k: float(v) for k, v in r.items()})
        except (TypeError, ValueError):
            continue
    if not out:
        sys.exit("no usable rows")
    return out


def launches(R):
    """Standing starts: stopped, then sustained acceleration."""
    found = []
    for i in range(1, len(R)):
        if R[i]["Speed"] <= 0.3:
            continue
        ahead = [x for x in R[i:] if x["recv_t"] - R[i]["recv_t"] <= 2.0]
        if not ahead or max(x["Speed"] for x in ahead) < 5.0:
            continue
        near = [x for x in R[i:] if x["recv_t"] - R[i]["recv_t"] <= 0.5]
        if near and min(x["Speed"] for x in near) <= 0.3:
            continue
        if found and R[i]["recv_t"] - found[-1] < 8.0:
            continue
        back = [x for x in R[max(0, i - 90):i] if x["Speed"] <= 0.3]
        if len(back) < 30:
            continue
        found.append(R[i]["recv_t"])
    return found


def trace(R, t0):
    """Cumulative distance after t0, by position if present, else by speed."""
    seg = [r for r in R if r["recv_t"] >= t0]
    have_pos = any(r.get("PositionX") or r.get("PositionZ") for r in seg)
    out, d, prev = [], 0.0, None
    for r in seg:
        if prev is not None:
            if have_pos:
                d += math.hypot(r.get("PositionX", 0) - prev.get("PositionX", 0),
                                r.get("PositionZ", 0) - prev.get("PositionZ", 0))
            else:
                d += 0.5 * (r["Speed"] + prev["Speed"]) * (r["recv_t"] - prev["recv_t"])
        out.append((r["recv_t"] - t0, d, r["Speed"]))
        prev = r
    return out, have_pos


def at_time(tr, want):
    """Distance and speed at an elapsed time, interpolated."""
    prev = None
    for t, d, v in tr:
        if t >= want:
            if prev is None:
                return d, v
            pt, pd, pv = prev
            f = (want - pt) / ((t - pt) or 1)
            return pd + (d - pd) * f, pv + (v - pv) * f
        prev = (t, d, v)
    return None, None


QUARTER = 402.336
MPS_TO_MPH = 2.2369362920544


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip().split("\n\n")[1].strip())
    path, times = sys.argv[1], [float(x) for x in sys.argv[2:]]
    R = load(path)
    starts = launches(R)
    print(f"\n{len(starts)} launches in the dump, {len(times)} times given")
    if not starts:
        sys.exit("no launches found - is this a raw dump from a driving session?")
    n = min(len(starts), len(times))
    if len(starts) != len(times):
        print("  (comparing the first "
              f"{n} of each - make sure they line up in order)")

    print(f"\n{'run':>4}{'game ET':>10}{'our dist':>11}{'vs 1/4mi':>11}"
          f"{'speed there':>13}")
    dists = []
    for i in range(n):
        tr, have_pos = trace(R, starts[i])
        d, v = at_time(tr, times[i])
        if d is None:
            print(f"{i+1:>4}{times[i]:>10.3f}   run ended before that time")
            continue
        dists.append((d, v))
        print(f"{i+1:>4}{times[i]:>10.3f}{d:>10.1f}m{d-QUARTER:>+10.1f}m"
              f"{v*MPS_TO_MPH:>11.1f}mph")

    if len(dists) < 2:
        return
    ds = [d for d, _ in dists]
    mean = sum(ds) / len(ds)
    spread = max(ds) - min(ds)
    sd = (sum((x - mean) ** 2 for x in ds) / len(ds)) ** 0.5
    # Judge in time, not distance. At 165 mph a few metres is a few
    # hundredths, and hundredths are exactly what a start-reference
    # difference looks like - so the same spread means different things at
    # different speeds.
    v_end = sum(v for _, v in dists) / len(dists)
    sd_time = sd / v_end if v_end else 0.0
    print(f"\nmean {mean:.1f} m   spread {spread:.1f} m   sd {sd:.1f} m"
          f"  ({sd_time*1000:.0f} ms at {v_end*MPS_TO_MPH:.0f} mph)")
    print(f"a quarter mile is {QUARTER:.1f} m\n")

    if sd_time < 0.02:
        print(f"Consistent. The strip measures about {mean:.0f} m from where")
        print("the car starts moving. Set that as the finish and our ET will")
        print(f"match the game:   --strip {mean:.1f}")
        if abs(mean - QUARTER) < 5:
            print("That is a quarter mile, so the distance was never the")
            print("problem - the remaining difference is where the clock starts.")
    else:
        print("Scattered, so no single distance reconciles these. The clocks")
        print("are starting at different moments rather than measuring")
        print("different lengths - most likely the game starts timing at the")
        print("green light while we start when the car first moves.")
        print("In that case our ET is correct as 'from first movement' and")
        print("should be reported as such rather than bent to match.")


if __name__ == "__main__":
    main()
