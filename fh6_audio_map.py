#!/usr/bin/env python3
"""
fh6_audio_map.py - see exactly what the countdown detector heard.

Takes a capture made by fh6_audio_probe.py and, for every launch in the
telemetry, reports whether the countdown was found, how far the chime stood
above the engine, and what rpm you launched at. That last column is the point:
it answers whether detection holds up across launch rpm, which is the open
question for anyone whose launch style differs from yours.

    python3 fh6_audio_map.py                       fh6_audio.wav + fh6_sync.csv
    python3 fh6_audio_map.py my.wav my.csv
    python3 fh6_audio_map.py --sweep               try several thresholds

Run it yourself after a session; nothing needs to be sent anywhere.
"""

import argparse
import csv
import os
import sys

import numpy as np
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import fh6_reaction as FR
except ImportError:
    sys.exit("put this next to fh6_reaction.py and fh6_draggy.py")


def load(wav_path, csv_path):
    try:
        w = wave.open(wav_path)
    except (OSError, wave.Error) as e:
        sys.exit(f"cannot read {wav_path}: {e}")
    sr = w.getframerate()
    a = np.frombuffer(w.readframes(w.getnframes()),
                      dtype="<i2").astype(np.float32) / 32768
    if sr != FR.RATE:
        sys.exit(f"expected {FR.RATE} Hz audio, got {sr}")
    try:
        rows = [{k: float(v) for k, v in r.items()}
                for r in csv.DictReader(open(csv_path))]
    except OSError as e:
        sys.exit(f"cannot read {csv_path}: {e}")
    if not rows or "mac_t" not in rows[0]:
        sys.exit(f"{csv_path} is not a probe capture (needs a mac_t column)")
    return a, sr, rows


def launches(R):
    """Stage held, then released, with the car moving immediately after."""
    out = []
    for x, b in zip(R, R[1:]):
        held = any(x[k] >= 200 for k in ("Clutch", "Brake", "HandBrake"))
        rel = all(b[k] < 40 for k in ("Clutch", "Brake", "HandBrake"))
        if held and rel and x["Speed"] < 1.0:
            t0 = b["mac_t"]
            mv = next((r["mac_t"] for r in R
                       if r["mac_t"] >= t0 and r["Speed"] > 0.3), None)
            if mv is not None and mv - t0 < 0.25:
                out.append((t0, mv))
    # de-duplicate launches detected on consecutive frames
    dedup = []
    for t0, mv in out:
        if not dedup or t0 - dedup[-1][0] > 3.0:
            dedup.append((t0, mv))
    return dedup


def detect(a, sr):
    cl = FR.ChimeListener()
    B = FR.HOP * 4
    for i in range(0, len(a) - B, B):
        cl.q.put(((i + B) / sr, a[i:i + B]))
        cl.pump()
    return cl.events


def snr_at(a, sr, t0, span=0.5):
    """How far the loudest 5200 Hz moment in a window sits above the floor."""
    NF = FR.NFFT
    win = np.hanning(NF).astype(np.float32)
    fr = np.fft.rfftfreq(NF, 1 / sr)
    band = np.where((fr >= FR.CHIME_LO) & (fr <= FR.CHIME_HI))[0]

    def mag(lo, hi):
        i0, i1 = max(0, int(lo * sr)), min(len(a), int(hi * sr))
        v = [np.abs(np.fft.rfft(a[i:i + NF] * win))[band].max()
             for i in range(i0, max(i0 + 1, i1 - NF), FR.HOP) if i + NF <= len(a)]
        return np.array(v) if v else np.array([0.0])

    floor = np.median(np.concatenate([mag(t0 - 6.0, t0 - 4.5),
                                      mag(t0 - 0.55, t0 - 0.35)]))
    best = max(mag(t0 - o - 0.22, t0 - o + 0.28).max() for o in (3.0, 2.0, 1.0))
    return best / (floor + 1e-12)


def launch_rpm(R, t0):
    v = [r["CurrentEngineRpm"] for r in R if t0 - 0.5 <= r["mac_t"] <= t0]
    return float(np.median(v)) if v else float("nan")


def report(a, sr, R, events, label=""):
    L = launches(R)
    if not L:
        print("no launches found in the telemetry")
        return []
    print(f"\n{label}{len(L)} launches, {len(events)} chime candidates\n")
    print(f"{'release':>9}{'rpm':>8}{'beeps':>7}{'GO-release':>12}{'chime/engine':>14}")
    rows = []
    for t0, mv in L:
        ch = FR.countdown_chain(events, t0)
        go = FR.predict_go(ch, t0)
        rpm = launch_rpm(R, t0)
        s = snr_at(a, sr, t0)
        rows.append((t0, rpm, len(ch), go, s))
        g = f"{go - t0:+.3f}s" if go else "not found"
        print(f"{t0:9.3f}{rpm:8.0f}{len(ch):7d}{g:>12}{s:13.1f}x")

    found = [r for r in rows if r[3] is not None]
    print(f"\ndetected {len(found)}/{len(rows)}")
    if len(found) > 1:
        offs = np.array([r[3] - r[0] for r in found])
        print(f"GO-release  mean {offs.mean():+.3f}s   sd {offs.std()*1000:.0f} ms")
        print("  (sd is the honest precision of a reaction time here)")
    if len(rows) > 2:
        lo = [r for r in rows if r[1] < 5000]
        hi = [r for r in rows if r[1] >= 5000]
        for name, grp in (("below 5000 rpm", lo), ("5000 rpm and up", hi)):
            if grp:
                ok = sum(1 for r in grp if r[3] is not None)
                sn = np.median([r[4] for r in grp])
                print(f"  {name:<16} {ok}/{len(grp)} found, median chime/engine {sn:.1f}x")
    return rows


def main():
    ap = argparse.ArgumentParser(description="map the countdown audio against telemetry")
    ap.add_argument("wav", nargs="?", default="fh6_audio.wav")
    ap.add_argument("csv", nargs="?", default="fh6_sync.csv")
    ap.add_argument("--sweep", action="store_true",
                    help="try a range of detection thresholds")
    a_ = ap.parse_args()

    a, sr, R = load(a_.wav, a_.csv)
    print(f"{len(a)/sr:.1f}s audio, {len(R)} telemetry rows, "
          f"peak {np.abs(a).max():.3f}, rms {np.sqrt((a**2).mean()):.5f}")
    if np.abs(a).max() < 0.02:
        print("  ! very quiet - turn the TV up or move the mic closer")

    if a_.sweep:
        original = FR.CHIME_RATIO
        for thr in (4.0, 3.0, 2.5, 2.0, 1.7):
            FR.CHIME_RATIO = thr
            report(a, sr, R, detect(a, sr), label=f"threshold {thr}:  ")
        FR.CHIME_RATIO = original
    else:
        report(a, sr, R, detect(a, sr))
    print()


if __name__ == "__main__":
    main()
