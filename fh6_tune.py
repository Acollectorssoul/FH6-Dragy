#!/usr/bin/env python3
"""
fh6_tune.py - find detector settings that hold up on sessions they were not
tuned on.

    python3 fh6_tune.py              every fh6_*.wav in this folder
    python3 fh6_tune.py ~/captures

Each session is a matching .wav and .csv pair from fh6_audio_probe.py.

WHY THIS EXISTS
    Sweeping parameters until detection looks good is easy and mostly
    worthless: with enough knobs, any setting can be made to fit one
    recording. What matters is whether it survives a room, a TV volume, or a
    launch style it has never seen.

    So this holds one session out, picks the best settings using only the
    others, and then scores those settings on the held-out one. Repeated for
    each session in turn. The held-out numbers are the ones to believe - the
    in-sample numbers are always flattering and should be ignored.

    With one session it cannot do this, and says so rather than reporting a
    number that means nothing.

WHAT IT OPTIMISES
    Detection rate first, then the spread of GO-minus-release. That spread is
    the real precision of a reaction time, so a setting that finds every
    launch sloppily is worse than one that finds most of them tightly.
"""

import argparse
import csv
import glob
import itertools
import os
import sys

import numpy as np
import wave

RATE = 44100
HOP = 128
BEEP_INTERVAL = 1.0
BEEP_TOL = 0.14

# the axes worth searching; each was a real failure mode at some point
GRID = {
    "nfft": (2048, 4096),
    "half": (10.0, 15.0, 25.0, 40.0),
    "thr": (1.7, 2.0, 2.5, 3.0),
    "debounce": (0.30, 0.35, 0.45),
}


def sessions(folder):
    out = []
    for wav in sorted(glob.glob(os.path.join(folder, "*.wav"))):
        base = os.path.splitext(wav)[0]
        for csv_path in (base + ".csv", os.path.join(folder, "fh6_sync.csv")):
            if os.path.exists(csv_path):
                out.append((wav, csv_path))
                break
    return out


def load(wav_path, csv_path):
    w = wave.open(wav_path)
    if w.getframerate() != RATE:
        return None
    a = np.frombuffer(w.readframes(w.getnframes()),
                      dtype="<i2").astype(np.float32) / 32768
    rows = [{k: float(v) for k, v in r.items()}
            for r in csv.DictReader(open(csv_path))]
    if not rows or "mac_t" not in rows[0]:
        return None
    return a, rows


def launches(R):
    out = []
    for x, b in zip(R, R[1:]):
        held = any(x[k] >= 200 for k in ("Clutch", "Brake", "HandBrake"))
        rel = all(b[k] < 40 for k in ("Clutch", "Brake", "HandBrake"))
        if held and rel and x["Speed"] < 1.0:
            t0 = b["mac_t"]
            mv = next((r["mac_t"] for r in R
                       if r["mac_t"] >= t0 and r["Speed"] > 0.3), None)
            if mv is not None and mv - t0 < 0.25:
                out.append(t0)
    dedup = []
    for t in out:
        if not dedup or t - dedup[-1] > 3.0:
            dedup.append(t)
    return dedup


def spectro(a, nfft):
    """Cached per (session, nfft) - the expensive part."""
    win = np.hanning(nfft).astype(np.float32)
    n = (len(a) - nfft) // HOP
    freqs = np.fft.rfftfreq(nfft, 1 / RATE)
    keep = np.where(np.abs(freqs - 5200) <= 60)[0]      # widest band we search
    S = np.empty((n, len(keep)), dtype=np.float32)
    for i in range(n):
        S[i] = np.abs(np.fft.rfft(a[i * HOP:i * HOP + nfft] * win))[keep]
    return S, freqs[keep]


def evaluate(S, sub_freqs, L, half, thr, debounce):
    band = np.abs(sub_freqs - 5200) <= half
    if not band.any():
        # narrower than the fft's bin spacing; fall back to the nearest bin
        band = np.zeros_like(band)
        band[int(np.argmin(np.abs(sub_freqs - 5200)))] = True
    mag = S[:, band].max(axis=1)
    dt = HOP / RATE
    k = int(2.0 / dt)
    ref = np.array([np.median(mag[max(0, i - k):i + 1]) for i in range(len(mag))])
    d = mag / (ref + 1e-9)

    cand, last = [], -9.0
    for i in range(1, len(d) - 1):
        t = i * dt
        if d[i] > thr and d[i] >= d[i - 1] and d[i] >= d[i + 1] and t - last > debounce:
            cand.append(t)
            last = t

    hits, offs = 0, []
    for t0 in L:
        seq = [e for e in cand if t0 - 6.0 <= e <= t0 + 0.6]
        best = []
        for i, s0 in enumerate(seq):
            ch = [s0]
            for t in seq[i + 1:]:
                if abs((t - ch[-1]) - BEEP_INTERVAL) < BEEP_TOL:
                    ch.append(t)
                elif t - ch[-1] > BEEP_INTERVAL + BEEP_TOL:
                    break
            if len(ch) < 3:
                continue
            while len(ch) > 3 and ch[-1] > t0 + 0.6:
                ch.pop()
            if not best or abs(ch[-1] - t0) < abs(best[-1] - t0):
                best = ch
        if best:
            go = best[-1] if best[-1] >= t0 - 0.5 else best[-1] + BEEP_INTERVAL
            hits += 1
            offs.append(go - t0)
    rate = hits / len(L) if L else 0.0
    sd = float(np.std(offs)) if len(offs) > 1 else float("nan")
    return rate, sd, len(L)


def score(rate, sd):
    """Detection first; among similar rates, prefer the tighter spread."""
    if np.isnan(sd):
        return (rate, -9.99)
    return (round(rate, 2), -sd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=".")
    a_ = ap.parse_args()

    pairs = sessions(a_.folder)
    if not pairs:
        sys.exit(f"no wav/csv session pairs found in {a_.folder}")

    data = []
    for wav, cs in pairs:
        got = load(wav, cs)
        if not got:
            print(f"  skipping {os.path.basename(wav)} (unreadable or wrong format)")
            continue
        a, R = got
        L = launches(R)
        if not L:
            print(f"  skipping {os.path.basename(wav)} (no launches)")
            continue
        data.append({"name": os.path.basename(wav), "a": a, "L": L, "S": {}})
        print(f"  {os.path.basename(wav)}: {len(L)} launches, {len(a)/RATE:.0f}s")

    if not data:
        sys.exit("no usable sessions")

    for d in data:
        for nf in GRID["nfft"]:
            d["S"][nf] = spectro(d["a"], nf)

    combos = list(itertools.product(GRID["nfft"], GRID["half"],
                                    GRID["thr"], GRID["debounce"]))
    print(f"\nevaluating {len(combos)} settings on {len(data)} session(s)\n")

    table = {}
    for c in combos:
        nf, half, thr, deb = c
        for d in data:
            S, f = d["S"][nf]
            table[(c, d["name"])] = evaluate(S, f, d["L"], half, thr, deb)

    if len(data) == 1:
        print("Only one session. Held-out validation is impossible, so these")
        print("numbers describe how well the settings fit this recording - not")
        print("how they will behave anywhere else. Capture another session.\n")
        best = max(combos, key=lambda c: score(*table[(c, data[0]['name'])][:2]))
        r, sd, n = table[(best, data[0]["name"])]
        print(f"in-sample best: nfft={best[0]} band=+-{best[1]:.0f}Hz "
              f"thr={best[2]} debounce={best[3]}")
        print(f"  {r*100:.0f}% of {n} launches, spread {sd*1000:.0f} ms")
        return

    print(f"{'held-out session':<26}{'chosen settings':<34}{'found':>8}{'sd ms':>8}")
    rates, sds = [], []
    for held in data:
        rest = [d for d in data if d is not held]
        def mean_score(c):
            rs = [table[(c, d["name"])] for d in rest]
            return (np.mean([x[0] for x in rs]),
                    -np.nanmean([x[1] if not np.isnan(x[1]) else 9.99 for x in rs]))
        best = max(combos, key=mean_score)
        r, sd, n = table[(best, held["name"])]
        rates.append(r)
        if not np.isnan(sd):
            sds.append(sd)
        cfg = f"n={best[0]} ±{best[1]:.0f}Hz t={best[2]} d={best[3]}"
        print(f"{held['name']:<26}{cfg:<34}{f'{r*100:.0f}%':>8}"
              f"{(f'{sd*1000:.0f}' if not np.isnan(sd) else '-'):>8}")

    print(f"\nheld-out mean: {np.mean(rates)*100:.0f}% detected"
          + (f", spread {np.mean(sds)*1000:.0f} ms" if sds else ""))
    print("These are the numbers to trust. If they are much worse than the")
    print("in-sample figures, the settings are fitting the recordings rather")
    print("than the signal.\n")

    def overall(c):
        rs = [table[(c, d["name"])] for d in data]
        return (np.mean([x[0] for x in rs]),
                -np.nanmean([x[1] if not np.isnan(x[1]) else 9.99 for x in rs]))
    b = max(combos, key=overall)
    print(f"settings to ship: CHIME_LO/HI = {5200-b[1]:.0f}/{5200+b[1]:.0f}, "
          f"CHIME_RATIO = {b[2]}, CHIME_GAP = {b[3]}, NFFT = {b[0]}")


if __name__ == "__main__":
    main()
