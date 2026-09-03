#!/usr/bin/env python3
"""
fh6_draggy.py - a Draggy for Forza Horizon 6 drag racing.

Listens to the game's built-in "Data Out" UDP telemetry and measures, per run:

  * Reaction time      - GO -> your inputs release -> car moves
  * Launch input spread- how far apart your clutch/brake/throttle releases were
  * 60ft / 330ft / 1/8 / 1000ft / 1/4 mile ET + trap speed
  * 0-30 / 0-60 / 0-100 mph
  * Wheelspin, peak power, shift points

Everything comes from one UDP stream on one clock, so there is no sync step.

SETUP
  FH6 -> Settings -> HUD and Gameplay -> Data Out: ON
                                      -> Data Out IP:   127.0.0.1  (or this PC's LAN IP if on console)
                                      -> Data Out Port: 5300  (avoid 5200-5300 range... use 5606)
  Then:  python3 fh6_draggy.py

  Higher game framerate = finer timing resolution. Packets arrive at your
  framerate, so 60fps gives ~16.7ms resolution and 120fps gives ~8.3ms.

USAGE
  python3 fh6_draggy.py                 # live capture
  python3 fh6_draggy.py --port 5606
  python3 fh6_draggy.py --raw           # also dump every packet to CSV (calibration)
  python3 fh6_draggy.py --history       # print saved runs + PBs, then exit
  python3 fh6_draggy.py --replay f.csv  # re-analyze a --raw dump
"""

import argparse
import csv
import hashlib
import io
import math
import json
import os
import socket
import struct
import sys
import time
from datetime import datetime

# --------------------------------------------------------------------------
# Packet layout - Forza Horizon 6 "Data Out", 324 bytes, little-endian.
# Field order per official Forza support documentation. FH6 inserts CarGroup,
# SmashableVelDiff and SmashableMass after NumCylinders. The struct accounts
# for 323 bytes; the final byte is trailing pad and is ignored.
# --------------------------------------------------------------------------

_FIELDS = [
    ("IsRaceOn", "i"), ("TimestampMS", "I"),
    ("EngineMaxRpm", "f"), ("EngineIdleRpm", "f"), ("CurrentEngineRpm", "f"),
    ("AccelerationX", "f"), ("AccelerationY", "f"), ("AccelerationZ", "f"),
    ("VelocityX", "f"), ("VelocityY", "f"), ("VelocityZ", "f"),
    ("AngularVelocityX", "f"), ("AngularVelocityY", "f"), ("AngularVelocityZ", "f"),
    ("Yaw", "f"), ("Pitch", "f"), ("Roll", "f"),
    ("SuspFL", "f"), ("SuspFR", "f"), ("SuspRL", "f"), ("SuspRR", "f"),
    ("SlipRatioFL", "f"), ("SlipRatioFR", "f"), ("SlipRatioRL", "f"), ("SlipRatioRR", "f"),
    ("WheelSpeedFL", "f"), ("WheelSpeedFR", "f"), ("WheelSpeedRL", "f"), ("WheelSpeedRR", "f"),
    ("RumbleFL", "i"), ("RumbleFR", "i"), ("RumbleRL", "i"), ("RumbleRR", "i"),
    ("PuddleFL", "i"), ("PuddleFR", "i"), ("PuddleRL", "i"), ("PuddleRR", "i"),
    ("SurfRumbleFL", "f"), ("SurfRumbleFR", "f"), ("SurfRumbleRL", "f"), ("SurfRumbleRR", "f"),
    ("SlipAngleFL", "f"), ("SlipAngleFR", "f"), ("SlipAngleRL", "f"), ("SlipAngleRR", "f"),
    ("CombSlipFL", "f"), ("CombSlipFR", "f"), ("CombSlipRL", "f"), ("CombSlipRR", "f"),
    ("SuspMetersFL", "f"), ("SuspMetersFR", "f"), ("SuspMetersRL", "f"), ("SuspMetersRR", "f"),
    ("CarOrdinal", "i"), ("CarClass", "i"), ("CarPerformanceIndex", "i"),
    ("DrivetrainType", "i"), ("NumCylinders", "i"),
    ("CarGroup", "I"), ("SmashableVelDiff", "f"), ("SmashableMass", "f"),
    ("PositionX", "f"), ("PositionY", "f"), ("PositionZ", "f"),
    ("Speed", "f"), ("Power", "f"), ("Torque", "f"),
    ("TireTempFL", "f"), ("TireTempFR", "f"), ("TireTempRL", "f"), ("TireTempRR", "f"),
    ("Boost", "f"), ("Fuel", "f"), ("DistanceTraveled", "f"),
    ("BestLap", "f"), ("LastLap", "f"), ("CurrentLap", "f"), ("CurrentRaceTime", "f"),
    ("LapNumber", "H"), ("RacePosition", "B"),
    ("Accel", "B"), ("Brake", "B"), ("Clutch", "B"), ("HandBrake", "B"), ("Gear", "B"),
    ("Steer", "b"), ("NormalizedDrivingLine", "b"), ("NormalizedAIBrakeDifference", "b"),
]

_FMT = "<" + "".join(t for _, t in _FIELDS)
_NAMES = [n for n, _ in _FIELDS]
_SIZE = struct.calcsize(_FMT)          # 323
_UNPACK = struct.Struct(_FMT).unpack_from


def parse(buf):
    """Decode one Data Out datagram into a dict, or None if it isn't one."""
    if len(buf) < _SIZE:
        return None
    return dict(zip(_NAMES, _UNPACK(buf, 0)))


# --------------------------------------------------------------------------
# Units and thresholds
# --------------------------------------------------------------------------

MPS_TO_MPH = 2.2369362920544
M_60FT = 18.288
M_330FT = 100.584
M_EIGHTH = 201.168
M_1000FT = 304.8
M_QUARTER = 402.336
M_HALF = 804.672
M_MILE = 1609.344

# Named strips, so a half or full mile is one word rather than a number nobody
# remembers. 100-200 mph only appears on the longer ones - a quarter mile ends
# before most cars see 200.
STRIPS = {"eighth": M_EIGHTH, "quarter": M_QUARTER,
          "half": M_HALF, "mile": M_MILE}

DIST_MARKS = [("60ft", M_60FT), ("330ft", M_330FT), ("1/8mi", M_EIGHTH),
              ("1000ft", M_1000FT), ("1/4mi", M_QUARTER),
              ("1/2mi", M_HALF), ("1mi", M_MILE)]
# Roll-on intervals, the ones Draggy ranks on. Measured between two speeds
# rather than from a standing start, so they compare cleanly across launches -
# a bad 60ft cannot flatter or ruin them.
ROLL_MARKS = [(60.0, 130.0), (100.0, 150.0), (100.0, 200.0), (150.0, 250.0),
              (60.0, 200.0), (60.0, 100.0)]

SPEED_MARKS = [("0-10", 10.0), ("0-20", 20.0), ("0-30", 30.0), ("0-40", 40.0),
               ("0-50", 50.0), ("0-60", 60.0), ("0-70", 70.0), ("0-80", 80.0),
               ("0-90", 90.0), ("0-100", 100.0), ("0-120", 120.0),
               ("0-150", 150.0), ("0-180", 180.0)]

HELD = 200          # input byte value counted as "held" (of 255)
RELEASED = 40       # input byte value counted as "released"
MOVING = 0.30       # m/s that counts as the car actually moving
STAGE_MIN_S = 0.40  # must hold the stage at least this long to count
FREE_STILL_S = 1.5  # free roam: seconds stopped before a launch counts
ROLL_LIFT = 200     # throttle must stay above this through a roll-on
RUN_TIMEOUT = 90.0  # seconds before an unfinished run is abandoned
                    # (a full mile takes far longer than a quarter)

HISTORY_PATH = os.path.expanduser("~/fh6_draggy_runs.json")
ROLLS_PATH = os.path.expanduser("~/fh6_rollons.json")
EVIDENCE_DIR = os.path.expanduser("~/fh6_runs")


def keep_evidence(run, samples):
    """
    Save the telemetry a run was derived from, and record its hash with the
    run.

    A result is only a number in a file, and a number in a file can be typed.
    Keeping the packets means any posted time can be re-derived from its own
    evidence, and the hash makes silent editing of either side visible. It
    does not stop a determined cheat - a replayed capture would still pass -
    but it makes a leaderboard built now still worth trusting later, which
    is not something that can be added retroactively.
    """
    try:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        cols = [c for c in RAW_COLS if c != "recv_t"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["t"] + cols, extrasaction="ignore")
        w.writeheader()
        for s in samples:
            row = {k: s.get(k) for k in cols}
            row["t"] = round(s["t"], 5)
            w.writerow(row)
        blob = buf.getvalue().encode()
        digest = hashlib.sha256(blob).hexdigest()

        # Named by content, not by clock. Two runs can share a timestamp to
        # the second - a replay produces several - and a name collision would
        # silently overwrite one run's evidence with another's, leaving a
        # stored hash that no longer matches anything.
        stamp = (run.get("timestamp") or "run").replace(":", "-")
        path = os.path.join(EVIDENCE_DIR, f"{stamp}_{digest[:8]}.csv")
        with open(path, "wb") as f:
            f.write(blob)
        run["evidence"] = os.path.basename(path)
        run["evidence_sha256"] = digest
    except OSError:
        pass                       # never lose a run over a failed write


def save_roll(entry):
    """Roll-ons live in their own file - they are not runs and have no ET."""
    try:
        rolls = json.load(open(ROLLS_PATH)) if os.path.exists(ROLLS_PATH) else []
    except (OSError, json.JSONDecodeError):
        rolls = []
    rolls.append(entry)
    try:
        json.dump(rolls, open(ROLLS_PATH, "w"), indent=1)
    except OSError:
        pass


def interp_time(t0, t1, v0, v1, target):
    """Linear-interpolate the time at which a value crosses target."""
    if v1 == v0:
        return t1
    return t0 + (t1 - t0) * (target - v0) / (v1 - v0)


def crossing(samples, key, target, scale=1.0):
    """First time `key*scale` reaches `target`. Returns seconds or None."""
    prev = None
    for s in samples:
        val = s[key] * scale
        if val >= target:
            if prev is None:
                return s["t"]
            return interp_time(prev["t"], s["t"], prev[key] * scale, val, target)
        prev = s
    return None


def release_time(samples, key):
    """
    Instant an input BEGAN to be released, in seconds. Returns (t, was_held).

    A trigger does not snap from 255 to 0; it falls over several frames. Taking
    a fixed threshold crossing therefore reports a time biased late by roughly
    half the travel - tens of milliseconds, which matters at this scale. Instead
    this fits a straight line through the falling samples and extrapolates back
    to the held value, recovering the moment your finger actually moved rather
    than the moment the value happened to cross an arbitrary level.
    """
    held_val = None
    ramp = []
    for s in samples:
        v = s[key]
        if v >= HELD:
            held_val = v
            ramp = []                     # still held; discard any prior partial ramp
        elif held_val is not None:
            ramp.append(s)
            if v <= RELEASED:
                break
    if held_val is None or not ramp:
        return None, held_val is not None

    pts = [(s[key], s["t"]) for s in ramp if s[key] < held_val]
    if len(pts) >= 2:
        n = len(pts)
        sv = sum(v for v, _ in pts)
        st = sum(t for _, t in pts)
        svv = sum(v * v for v, _ in pts)
        svt = sum(v * t for v, t in pts)
        denom = n * svv - sv * sv
        if denom != 0:
            a = (n * svt - sv * st) / denom      # dt/dvalue
            b = (st - a * sv) / n
            return a * held_val + b, True

    # single ramp sample: fall back to interpolating from the last held frame
    prev = None
    for s in samples:
        if s[key] >= HELD:
            prev = s
        elif prev is not None:
            return interp_time(prev["t"], s["t"], prev[key], s[key], held_val), True
    return ramp[0]["t"], True


# --------------------------------------------------------------------------
# Run analysis
# --------------------------------------------------------------------------

def analyze(samples, go_t, go_source, stage_info):
    """
    samples: list of dicts with 't' (seconds, game clock) plus packet fields,
             covering the stage through the end of the run.
    go_t:    seconds on the same clock where GO happened, or None.
    """
    r = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "go_source": go_source,
        "car_ordinal": samples[0]["CarOrdinal"],
        "car_pi": samples[0]["CarPerformanceIndex"],
        "drivetrain": ["FWD", "RWD", "AWD"][samples[0]["DrivetrainType"]]
        if samples[0]["DrivetrainType"] in (0, 1, 2) else "?",
        "sample_hz": None,
        "notes": [],
    }

    # Effective packet rate = timing resolution. Report it honestly.
    if len(samples) > 10:
        span = samples[-1]["t"] - samples[0]["t"]
        if span > 0:
            r["sample_hz"] = round((len(samples) - 1) / span, 1)
            r["resolution_ms"] = round(1000.0 * span / (len(samples) - 1), 1)

    r["stage_held"] = stage_info

    # --- movement -------------------------------------------------------
    # The launch is the start of SUSTAINED acceleration, not the first flicker
    # of speed. Creeping forward into the staging beam also crosses the
    # movement threshold, and treating that as the launch throws every
    # downstream number out by however long you then sat there.
    move_t = None
    for i, s0 in enumerate(samples):
        if s0["Speed"] <= MOVING:
            continue
        ahead = [x for x in samples[i:] if x["t"] - s0["t"] <= 2.0]
        if not ahead or max(x["Speed"] for x in ahead) < 5.0:
            continue                      # a creep, not a launch
        near = [x for x in samples[i:] if x["t"] - s0["t"] <= 0.5]
        if near and min(x["Speed"] for x in near) <= MOVING:
            continue                      # speed fell back; still shuffling
        prev = samples[i - 1] if i else None
        move_t = (interp_time(prev["t"], s0["t"], prev["Speed"], s0["Speed"], MOVING)
                  if prev is not None and prev["Speed"] <= MOVING else s0["t"])
        break
    r["first_movement_t"] = round(move_t, 4) if move_t is not None else None

    # --- input releases -------------------------------------------------
    # Only the launch itself counts. Without this window the throttle lift
    # after the finish line reads as a "release" seconds later.
    # Only the seconds either side of the launch count. Anything earlier is
    # you shuffling into the beam, anything later is the lift after the finish;
    # both look exactly like a launch release if the window is left open.
    win = ([s for s in samples
            if move_t - 1.5 <= s["t"] <= move_t + 0.25]
           if move_t is not None else samples)
    releases = {}
    for label, key in (("throttle", "Accel"), ("brake", "Brake"),
                       ("clutch", "Clutch"), ("handbrake", "HandBrake")):
        t, held = release_time(win, key)
        if held and t is not None:
            releases[label] = t

    r["releases"] = {k: round(v, 4) for k, v in releases.items()}

    # Spread between the released inputs - how simultaneous your launch was.
    real = dict(releases)
    if len(real) >= 2:
        first_k = min(real, key=real.get)
        last_k = max(real, key=real.get)
        r["release_spread_ms"] = round((real[last_k] - real[first_k]) * 1000.0, 1)
        r["release_order"] = sorted(real, key=real.get)
        r["release_offsets_ms"] = {
            k: round((v - real[first_k]) * 1000.0, 1) for k, v in real.items()
        }
    if move_t is not None and real:
        r["release_to_movement_ms"] = round(
            (move_t - min(real.values())) * 1000.0, 1)

    # The instant the driver committed to the launch. For anyone holding the
    # car on an input that is the release; for a player held by the game with
    # nothing to let go of, it is the moment the car moved. Reaction time is
    # measured to this either way, so every launch style gets a number.
    r["launch_ref"] = round(min(real.values()) if real else move_t, 4) \
        if (real or move_t is not None) else None
    r["launch_ref_kind"] = ("input release" if real else
                            "first movement" if move_t is not None else None)

    # --- reaction time --------------------------------------------------
    if go_t is not None:
        r["go_t"] = round(go_t, 4)
        if real:
            first_rel = min(real.values())
            r["reaction_ms"] = round((first_rel - go_t) * 1000.0, 1)
            # FH6 restarts you for leaving early, so a negative reaction is a
            # dead run, not a quick one. Recording it as a best would reward
            # exactly the thing the game disallows.
            if r["reaction_ms"] < 0:
                r["foul"] = True
                r["notes"].append(
                    "RED - left before the green. The game restarts this run, "
                    "so it does not count toward bests.")
            r["reaction_input"] = min(real, key=real.get)
            r["all_inputs_clear_ms"] = round((max(real.values()) - go_t) * 1000.0, 1)
        if move_t is not None:
            r["go_to_movement_ms"] = round((move_t - go_t) * 1000.0, 1)
    else:
        r["notes"].append(
            "No GO marker in FH6 telemetry - times are measured from first "
            "movement, not from the green light."
        )

    # --- acceleration and ET, measured from first movement --------------
    if move_t is None:
        r["notes"].append("Car never moved; no ET computed.")
        return r

    launch = [s for s in samples if s["t"] >= move_t]
    if not launch:
        return r
    # FH6 never populates DistanceTraveled, so distance has to be derived.
    # Two independent ways: integrate speed, or add up how far the car
    # actually moved in world coordinates. Position is ground truth - it is
    # not subject to integration drift when the frame rate dips - so it wins
    # when available, and the two are compared so any disagreement is visible
    # rather than silent.
    d_speed = d_pos = 0.0
    have_pos = any(s.get("PositionX") or s.get("PositionZ") for s in launch)
    prev = None
    for s in launch:
        if prev is not None:
            d_speed += 0.5 * (s["Speed"] + prev["Speed"]) * (s["t"] - prev["t"])
            if have_pos:
                dx = s.get("PositionX", 0.0) - prev.get("PositionX", 0.0)
                dz = s.get("PositionZ", 0.0) - prev.get("PositionZ", 0.0)
                d_pos += math.hypot(dx, dz)
        s["_d"] = d_pos if have_pos else d_speed
        prev = s
    r["distance_source"] = "position" if have_pos else "speed integration"
    if have_pos and d_speed > 0:
        r["distance_agreement_pct"] = round(100.0 * d_pos / d_speed, 2)
        if abs(d_pos - d_speed) / d_speed > 0.02:
            r["notes"].append(
                f"Distance from position and from speed differ by "
                f"{abs(d_pos-d_speed):.1f} m over the run - times may be off.")
    if launch:
        r["start_pos"] = [round(launch[0].get("PositionX", 0.0), 1),
                          round(launch[0].get("PositionZ", 0.0), 1)]
        r["end_pos"] = [round(launch[-1].get("PositionX", 0.0), 1),
                        round(launch[-1].get("PositionZ", 0.0), 1)]

    r["et"] = {}
    for label, metres in DIST_MARKS:
        t = crossing(launch, "_d", metres)
        if t is None:
            continue
        entry = {"s": round(t - move_t, 4)}
        # trap speed at that marker
        prev = None
        for s in launch:
            if s["_d"] >= metres:
                if prev is None:
                    entry["mph"] = round(s["Speed"] * MPS_TO_MPH, 1)
                else:
                    v = interp_time(prev["Speed"], s["Speed"], prev["_d"], s["_d"], metres)
                    entry["mph"] = round(v * MPS_TO_MPH, 1)
                break
            prev = s
        if go_t is not None:
            entry["from_go_s"] = round(t - go_t, 4)
        r["et"][label] = entry

    r["roll"] = {}
    for lo, hi in ROLL_MARKS:
        t_lo = crossing(launch, "Speed", lo / MPS_TO_MPH)
        t_hi = crossing(launch, "Speed", hi / MPS_TO_MPH)
        if t_lo is not None and t_hi is not None and t_hi > t_lo:
            r["roll"][f"{lo:.0f}-{hi:.0f}"] = round(t_hi - t_lo, 3)

    r["accel"] = {}
    for label, mph in SPEED_MARKS:
        t = crossing(launch, "Speed", mph / MPS_TO_MPH)
        if t is not None:
            e = {"s": round(t - move_t, 4)}
            if go_t is not None:
                e["from_go_s"] = round(t - go_t, 4)
            r["accel"][label] = e

    # --- launch quality -------------------------------------------------
    window = [s for s in launch if s["t"] - move_t <= 2.0]
    if window:
        drive = samples[0]["DrivetrainType"]
        keys = (["SlipRatioFL", "SlipRatioFR"] if drive == 0 else
                ["SlipRatioRL", "SlipRatioRR"] if drive == 1 else
                ["SlipRatioFL", "SlipRatioFR", "SlipRatioRL", "SlipRatioRR"])
        peak = max(max(abs(s[k]) for k in keys) for s in window)
        r["peak_wheelspin"] = round(peak, 2)
        if peak > 1.0:
            r["notes"].append(
                f"Wheelspin off the line (slip {peak:.2f}) - losing time to traction."
            )

    # Tyre temperature at the moment of launch, driven wheels only. This is
    # the variable behind most 60ft scatter, and unlike a real Draggy we can
    # actually measure it - so runs become comparable instead of "that one was
    # on cold tyres".
    at_launch = [s for s in samples if abs(s["t"] - move_t) < 0.25] or samples[-1:]
    drive = samples[0]["DrivetrainType"]
    keys = (["TireTempFL", "TireTempFR"] if drive == 0 else
            ["TireTempRL", "TireTempRR"] if drive == 1 else
            ["TireTempFL", "TireTempFR", "TireTempRL", "TireTempRR"])
    temps = [s[k] for s in at_launch for k in keys if k in s]
    if temps and max(temps) > 0:
        r["tire_temp"] = round(sum(temps) / len(temps), 1)
    stage = [s for s in samples if s["t"] < move_t]
    if stage:
        r["launch_rpm"] = round(stage[-1]["CurrentEngineRpm"])

    # A coarse speed/accel trace, enough to draw the run without bloating the
    # history file. ~80 points covers a quarter mile at useful resolution.
    if launch:
        span = launch[-1]["t"] - launch[0]["t"]
        step = max(1, len(launch) // 80)
        trace = []
        prev = None
        for x in launch[::step]:
            g = 0.0
            if prev is not None and x["t"] > prev["t"]:
                g = (x["Speed"] - prev["Speed"]) / (x["t"] - prev["t"]) / 9.80665
            trace.append([round(x["t"] - move_t, 3),
                          round(x["Speed"] * MPS_TO_MPH, 1),
                          round(g, 2)])
            prev = x
        r["trace"] = trace
        r["run_seconds"] = round(span, 2)

    r["peak_power_hp"] = round(max(s["Power"] for s in launch) / 745.7, 1)
    r["top_speed_mph"] = round(max(s["Speed"] for s in launch) * MPS_TO_MPH, 1)

    shifts = []
    prev_gear = launch[0]["Gear"]
    for s in launch:
        if s["Gear"] > prev_gear:
            shifts.append({"gear": s["Gear"],
                           "at_s": round(s["t"] - move_t, 3),
                           "mph": round(s["Speed"] * MPS_TO_MPH, 1)})
            prev_gear = s["Gear"]
    r["shifts"] = shifts
    return r


# --------------------------------------------------------------------------
# Run detection state machine
# --------------------------------------------------------------------------

class Detector:
    """
    Watches the packet stream and emits a completed run dict.

    Stage  : car stopped with throttle+brake+clutch all held.
    GO     : detected from the telemetry itself. Two candidates are watched,
             and whichever fires is recorded along with which one it was, so
             the source is always visible rather than assumed.
    Run    : from GO (or first movement) until 1/4 mile or the car stops.
    """

    def __init__(self, on_run, finish=M_QUARTER, mode="strip", on_roll=None):
        """
        mode="strip"    a staged launch: stopped with throttle held, as at a
                        drag strip, where a countdown gives a reaction time.
        mode="anywhere" a real Draggy: any standing start after the car has
                        been still a moment, anywhere on the map. No countdown
                        exists, so no reaction time is claimed.
        """
        self.on_run = on_run
        self.on_roll = on_roll
        self.finish = finish
        self.mode = mode
        self.reset()

    def reset(self):
        self.state = "idle"
        self.samples = []
        self.stage_start = None
        self.go_t = None
        self.go_source = None
        self.prev = None
        self.stage_info = None
        self.holders = []
        self.trav = 0.0
        self.still_since = None
        self.roll = None

    def watch_roll(self, p, t):
        """
        Roll-on intervals without stopping - the other half of what a Draggy
        does. Starts when the throttle is pinned at or above the low speed and
        only counts if it stays pinned all the way to the high speed.
        """
        if self.on_roll is None:
            return
        mph = p["Speed"] * MPS_TO_MPH
        if self.roll is None:
            for lo, hi in ROLL_MARKS:
                if p["Accel"] >= ROLL_LIFT and lo <= mph < lo + 3:
                    self.roll = {"lo": lo, "hi": hi, "t0": t}
                    break
            return
        if p["Accel"] < ROLL_LIFT or mph < self.roll["lo"] - 5:
            self.roll = None                      # lifted or fell back
            return
        if mph >= self.roll["hi"]:
            self.on_roll({"pair": f"{self.roll['lo']:.0f}-{self.roll['hi']:.0f}",
                          "seconds": round(t - self.roll["t0"], 3),
                          "gear": int(p["Gear"]),
                          "timestamp": datetime.now().isoformat(timespec="seconds")})
            self.roll = None

    def feed(self, p, t):
        s = dict(p)
        s["t"] = t
        prev = self.prev
        self.prev = s
        self.watch_roll(p, t)

        # A stage is: stopped, throttle pinned, and at least one input holding
        # the car. Which input that is depends on your bindings - measured data
        # shows clutch+handbrake, with the Brake byte never used - so this does
        # not hardcode a trio.
        holders = [k for k in ("Brake", "Clutch", "HandBrake") if p[k] >= HELD]
        if holders:
            self.holders = holders
        # No holding input is required. A player using assists may hold only
        # throttle and be released by the game, in which case there is no
        # release to time and first movement becomes the reference instead.
        if p["Speed"] < MOVING:
            if self.still_since is None:
                self.still_since = t
        else:
            self.still_since = None

        if self.mode == "anywhere":
            # No inputs required. Sitting still for a moment then going is
            # exactly what a Draggy waits for.
            staged_now = (p["Speed"] < MOVING and self.still_since is not None
                          and t - self.still_since >= FREE_STILL_S)
        else:
            staged_now = (p["Speed"] < MOVING and p["Accel"] >= HELD)

        if self.state == "idle":
            if staged_now:
                self.state = "staged"
                self.stage_start = t
                self.samples = [s]
            return

        self.samples.append(s)

        if self.state == "staged":
            # GO candidate A: race clock starts ticking
            if prev is not None and prev["CurrentRaceTime"] <= 0.0 < p["CurrentRaceTime"]:
                self.go_t = interp_time(prev["t"], t, prev["CurrentRaceTime"],
                                        p["CurrentRaceTime"], 0.0)
                self.go_source = "CurrentRaceTime crossed 0"
            # GO candidate B: race flag flips on
            elif prev is not None and prev["IsRaceOn"] == 0 and p["IsRaceOn"] == 1:
                self.go_t = t
                self.go_source = "IsRaceOn 0->1"

            held_for = t - self.stage_start
            if p["Speed"] > MOVING:
                if held_for < STAGE_MIN_S:
                    self.reset()          # rolled through, not a real stage
                    return
                self.stage_info = {"held_s": round(held_for, 3),
                                   "inputs": self.holders}
                self.state = "running"
                self.trav = 0.0
                self.run_start = t
            return

        if self.state == "running":
            if prev is not None:
                self.trav += 0.5 * (p["Speed"] + prev["Speed"]) * (t - prev["t"])
            travelled = self.trav
            done = (travelled >= self.finish + 20
                    or (travelled > M_60FT and p["Speed"] < MOVING)
                    or t - self.run_start > RUN_TIMEOUT)
            if done:
                run = analyze(self.samples, self.go_t, self.go_source, self.stage_info)
                run["mode"] = self.mode
                keep_evidence(run, self.samples)
                self.reset()
                self.on_run(run)


# --------------------------------------------------------------------------
# History and personal bests
# --------------------------------------------------------------------------

def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_run(run):
    runs = load_history()
    runs.append(run)
    with open(HISTORY_PATH, "w") as f:
        json.dump(runs, f, indent=1)
    return runs


def bests(runs):
    """Lowest-is-better PBs across saved runs."""
    out = {}
    def consider(key, val, run):
        if val is None:
            return
        if key not in out or val < out[key][0]:
            out[key] = (val, run.get("timestamp", "?"))
    for run in runs:
        if run.get("foul"):
            continue          # a red light is not a personal best of anything
        consider("reaction_ms", run.get("reaction_ms"), run)
        consider("release_spread_ms", run.get("release_spread_ms"), run)
        for k, v in (run.get("accel") or {}).items():
            consider(k, v.get("s"), run)
        for k, v in (run.get("et") or {}).items():
            consider(k, v.get("s"), run)
    return out


def fmt_run(run, runs):
    L = []
    A = L.append
    A("")
    A("=" * 58)
    A(f"  RUN  {run['timestamp']}   #{len(runs)}")
    A("=" * 58)

    if run.get("sample_hz"):
        A(f"  telemetry {run['sample_hz']} Hz  (timing resolution "
          f"~{run.get('resolution_ms', '?')} ms)")

    A("")
    A("  LAUNCH")
    if run.get("foul"):
        A("")
        A("  *** RED LIGHT - left early, run does not count ***")
    if "reaction_ms" in run:
        A(f"    reaction (GO -> {run['reaction_input']} release) : "
          f"{run['reaction_ms']:>8.1f} ms   [{run['go_source']}]")
        A(f"    GO -> all inputs clear                : "
          f"{run['all_inputs_clear_ms']:>8.1f} ms")
    if "go_to_movement_ms" in run:
        A(f"    GO -> car moves                       : "
          f"{run['go_to_movement_ms']:>8.1f} ms")
    if "release_to_movement_ms" in run:
        A(f"    release -> car moves                   : "
          f"{run['release_to_movement_ms']:>8.1f} ms")
    if "release_spread_ms" in run:
        A(f"    input release spread                  : "
          f"{run['release_spread_ms']:>8.1f} ms")
        order = " -> ".join(run["release_order"])
        A(f"    release order                         : {order}")
        for k, v in sorted(run["release_offsets_ms"].items(), key=lambda x: x[1]):
            A(f"        {k:<10} +{v:.1f} ms")
    if run.get("peak_wheelspin") is not None:
        A(f"    peak wheelspin (slip ratio)           : "
          f"{run['peak_wheelspin']:>8.2f}")
    if run.get("launch_rpm"):
        A(f"    launch rpm                            : "
          f"{run['launch_rpm']:>8d}")
    if run.get("tire_temp") is not None:
        A(f"    tyre temp at launch                   : "
          f"{run['tire_temp']:>8.1f}")

    if run.get("accel"):
        A("")
        A("  ACCELERATION (from first movement)")
        for k, v in run["accel"].items():
            extra = f"   ({v['from_go_s']:.3f} s from GO)" if "from_go_s" in v else ""
            A(f"    {k+' mph':<12} {v['s']:>7.3f} s{extra}")

    if run.get("roll"):
        A("")
        A("  ROLL-ON")
        for k, v in run["roll"].items():
            A(f"    {k+' mph':<12} {v:>7.3f} s")

    if run.get("et"):
        A("")
        A("  ET")
        for k, v in run["et"].items():
            mph = f"  @ {v['mph']:>6.1f} mph" if "mph" in v else ""
            A(f"    {k:<12} {v['s']:>7.3f} s{mph}")

    A("")
    A(f"  trap/top {run.get('top_speed_mph', '?')} mph   "
      f"peak {run.get('peak_power_hp', '?')} hp   "
      f"{run.get('drivetrain', '?')} PI {run.get('car_pi', '?')}")

    pb = bests(runs)
    hits = []
    if run.get("reaction_ms") is not None and \
       pb.get("reaction_ms", (None,))[0] == run["reaction_ms"]:
        hits.append("reaction")
    for k, v in (run.get("accel") or {}).items():
        if pb.get(k, (None,))[0] == v.get("s"):
            hits.append(k)
    for k, v in (run.get("et") or {}).items():
        if pb.get(k, (None,))[0] == v.get("s"):
            hits.append(k)
    if hits:
        A("")
        A("  *** PERSONAL BEST: " + ", ".join(hits) + " ***")

    for n in run.get("notes", []):
        A(f"  ! {n}")
    A("")
    return "\n".join(L)


def print_history():
    runs = load_history()
    if not runs:
        print("No saved runs yet.")
        return
    print(f"\n{len(runs)} saved runs -> {HISTORY_PATH}\n")
    print(f"{'#':<4}{'when':<20}{'react':>9}{'spread':>9}{'0-60':>8}{'1/4mi':>9}{'trap':>8}")
    print("-" * 67)
    for i, r in enumerate(runs, 1):
        rx = r.get("reaction_ms")
        sp = r.get("release_spread_ms")
        s60 = (r.get("accel") or {}).get("0-60", {}).get("s")
        q = (r.get("et") or {}).get("1/4mi", {})
        qs, qm = q.get("s"), q.get("mph")
        react = f"{rx:.0f}ms" if rx is not None else "-"
        spread = f"{sp:.0f}ms" if sp is not None else "-"
        c60 = f"{s60:.3f}" if s60 is not None else "-"
        cq = f"{qs:.3f}" if qs is not None else "-"
        ct = f"{qm:.1f}" if qm is not None else "-"
        when = r.get("timestamp", "?")[:19]
        print(f"{i:<4}{when:<20}{react:>9}{spread:>9}{c60:>8}{cq:>9}{ct:>8}")
    print("\nPERSONAL BESTS")
    for k, (v, when) in sorted(bests(runs).items()):
        unit = "ms" if k.endswith("_ms") else "s"
        print(f"  {k:<20}{v:>10.3f} {unit:<3} ({when[:19]})")
    print()


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

RAW_COLS = ["recv_t", "TimestampMS", "IsRaceOn", "CurrentRaceTime", "Speed",
            "DistanceTraveled", "Accel", "Brake", "Clutch", "HandBrake",
            "Gear", "CurrentEngineRpm", "Power",
            "SlipRatioRL", "SlipRatioRR", "SlipRatioFL", "SlipRatioFR",
            "TireTempFL", "TireTempFR", "TireTempRL", "TireTempRR",
            "PositionX", "PositionY", "PositionZ"]


def listen(port, raw_path=None, finish=M_QUARTER, mode="strip", driver=""):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"Could not bind UDP {port}: {e}\n"
              f"Another telemetry app may already hold it. Only one listener per port.")
        return
    sock.settimeout(1.0)

    runs = load_history()

    def on_run(run):
        nonlocal runs
        if driver:
            run["driver"] = driver
        runs = save_run(run)
        print(fmt_run(run, runs))
        print("  ready - stage the next run\n")

    def on_roll(r):
        if driver:
            r["driver"] = driver
        save_roll(r)
        print(f"  roll-on {r['pair']} mph : {r['seconds']:.3f} s  "
              f"(gear {r['gear']})")

    det = Detector(on_run, finish, mode, on_roll)

    rawf = raww = None
    if raw_path:
        rawf = open(raw_path, "w", newline="")
        raww = csv.DictWriter(rawf, fieldnames=RAW_COLS, extrasaction="ignore")
        raww.writeheader()
        print(f"raw dump -> {raw_path}")

    print(f"listening on UDP {port} ... waiting for Forza Horizon 6")
    print("stage your launch as normal; runs print automatically. Ctrl-C to stop.\n")

    seen = False
    base = None
    last_wrap = 0
    prev_ms = None
    idle_note = time.time()

    try:
        while True:
            try:
                buf, _ = sock.recvfrom(2048)
            except socket.timeout:
                if not seen and time.time() - idle_note > 10:
                    print("  (no packets yet - check Data Out is ON and the IP/port match)")
                    idle_note = time.time()
                continue

            p = parse(buf)
            if p is None:
                continue
            if not seen:
                seen = True
                print(f"  telemetry connected ({len(buf)} byte packets)\n")

            # game clock in seconds, handling U32 millisecond wrap
            ms = p["TimestampMS"]
            if prev_ms is not None and ms < prev_ms - 1000:
                last_wrap += 2 ** 32
            prev_ms = ms
            t = (ms + last_wrap) / 1000.0
            if base is None:
                base = t
            t -= base

            if raww:
                row = {k: p.get(k) for k in RAW_COLS}
                row["recv_t"] = round(t, 4)
                raww.writerow(row)

            det.feed(p, t)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        if rawf:
            rawf.close()
        sock.close()


def replay(path):
    """Re-analyze a --raw dump. Useful for checking GO detection after the fact."""
    runs = load_history()
    def on_run(run):
        nonlocal runs
        runs = save_run(run)
        print(fmt_run(run, runs))

    def on_roll(r):
        save_roll(r)
        print(f"  roll-on {r['pair']} mph : {r['seconds']:.3f} s  "
              f"(gear {r['gear']})")

    det = Detector(on_run, M_QUARTER, "strip", on_roll)
    with open(path) as f:
        for row in csv.DictReader(f):
            p = {}
            for k, v in row.items():
                if k == "recv_t":
                    continue
                try:
                    p[k] = float(v) if "." in v or "e" in v.lower() else int(v)
                except ValueError:
                    p[k] = 0
            for k, _ in _FIELDS:
                p.setdefault(k, 0)
            det.feed(p, float(row["recv_t"]))
    print("replay complete.")


def main():
    ap = argparse.ArgumentParser(description="Draggy for Forza Horizon 6")
    ap.add_argument("--port", type=int, default=5606,
                    help="UDP port matching Data Out (default 5606)")
    ap.add_argument("--raw", nargs="?", const="fh6_raw.csv", default=None,
                    help="also dump every packet to CSV for calibration")
    ap.add_argument("--driver", default="", help="name recorded with each run")
    ap.add_argument("--anywhere", action="store_true",
                    help="measure any standing start on the map, not just a "
                         "staged launch at a strip")
    ap.add_argument("--strip", default="quarter",
                    help="eighth, quarter, half, mile, or a distance in metres")
    ap.add_argument("--history", action="store_true", help="print saved runs and PBs")
    ap.add_argument("--replay", help="re-analyze a raw CSV dump")
    a = ap.parse_args()

    if isinstance(a.strip, str) and a.strip not in STRIPS:
        try:
            a.strip = float(a.strip)
        except ValueError:
            sys.exit(f"--strip must be one of {', '.join(STRIPS)} or a number")

    if a.history:
        print_history()
    elif a.replay:
        replay(a.replay)
    else:
        listen(a.port, a.raw,
               STRIPS.get(a.strip, M_QUARTER)
               if isinstance(a.strip, str) else a.strip,
               "anywhere" if a.anywhere else "strip", a.driver)


if __name__ == "__main__":
    main()
