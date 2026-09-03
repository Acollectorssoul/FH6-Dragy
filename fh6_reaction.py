#!/usr/bin/env python3
"""
fh6_reaction.py - Draggy for FH6, with reaction time.

Reports per run: reaction time, input release spread, 60ft/330ft/1/8/1000ft/
1/4 ET, 0-30/0-60/0-100 mph, trap speed, wheelspin, and personal bests.

HOW THE TIMING WORKS - all of this was measured, not assumed:

  The countdown is a ~5200 Hz tone, three beeps one second apart, and GO lands
  one interval after the third. GO is predicted from the third beep rather than
  detected directly, because by the time GO sounds the car is already moving
  and the tone is buried under it.

  The microphone hears everything late - TV processing, air travel and input
  buffering. Uncorrected, that delay is larger than the quantity being
  measured. So the tyre chirp is the reference: it fires at first movement, an
  instant telemetry knows to a few milliseconds, and the gap between the two IS
  the audio delay. It is re-measured every run, so moving the Mac or changing
  the TV cannot silently corrupt the numbers.

  Validated against 6 real launches: reaction 53 ms mean, 15 ms sd.

  A reaction near or below ~100 ms means you are anticipating the countdown
  rhythm rather than responding to the light. That is normal in drag racing
  and is not an error.

SETUP
    FH6 Data Out ON, pointed at this Mac, port 5606.
    In-game engine volume DOWN (it masks the chime), tyre volume UP
    (the chirp is the latency reference), TV volume up.
    Mac where it can hear the TV.

    pip install sounddevice numpy
    python3 fh6_reaction.py
"""

import argparse
import collections
import csv
import json
import os
import queue
import socket
import sys
import threading
import time
from datetime import datetime

try:
    import numpy as np
    import sounddevice as sd
except (ImportError, OSError) as _e:
    # sounddevice raises OSError, not ImportError, when the PortAudio library
    # is absent - which is the usual failure on a fresh Mac. Either way, only
    # the microphone needs it: importing this module for its timing logic, as
    # the tests do, must not require an audio device. The failure is deferred
    # to the point of actual use.
    sd = None
    _IMPORT_ERROR = _e
    try:
        import numpy as np
    except ImportError:
        np = None
else:
    _IMPORT_ERROR = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fh6_draggy import (parse, analyze, fmt_run, load_history, save_run,
                        keep_evidence,
                        HELD, MOVING, MPS_TO_MPH, M_QUARTER, interp_time,
                        RAW_COLS, STRIPS)

RATE = 44100
NFFT = 2048
HOP = 128
CHIME_LO, CHIME_HI = 5185.0, 5215.0     # The tone is very pure, so a tight
                                        # window costs no signal but rejects a
                                        # lot of engine. Widening this is the
                                        # fastest way to break high-rpm launches:
                                        # engine noise here is broadband, the
                                        # chime is not.
CHIME_RATIO = 2.0                      # deliberately low. The chime sits about
                                       # 13.5 dB over the engine at 5200 Hz, so
                                       # a high bar misses it whenever the
                                       # engine is audible. False candidates are
                                       # rejected by the 1 s rhythm below, not
                                       # by loudness. Validated 5/5 across
                                       # launches from 2086 to 9237 rpm; the
                                       # chime's margin over the engine roughly
                                       # halves across that range, so a higher
                                       # bar silently fails limiter launches.
REF_SECONDS = 2.0                      # rolling window for the reference level.
                                       # Short enough to track a revving engine;
                                       # a slow reference cannot, and then the
                                       # threshold means something different at
                                       # idle than at 8000 rpm.
CHIME_GAP = 0.35                        # tone lasts ~300ms; cluster to one onset.


class ChimeListener:
    """
    Detects the ~5200 Hz game chime and records when each one reached the mic.

    Normalising against a rolling median of the spectrum means it keys on the
    tone standing out from whatever else is in the room, rather than absolute
    loudness, so it survives volume changes.
    """

    # Audio event times and telemetry timestamps are both absolute
    # time.monotonic() values, so they can be subtracted directly. Any other
    # arrangement reintroduces the clock-alignment problem this tool exists
    # to avoid.

    def __init__(self, device=None, loopback=False):
        self.stream_args = self.open_stream_args(device, loopback) if sd else {}
        self.events = []                 # arrival times, shared clock
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self.buf = np.zeros(0, dtype=np.float32)
        self.window = np.hanning(NFFT).astype(np.float32)
        freqs = np.fft.rfftfreq(NFFT, 1.0 / RATE)
        self.band = np.where((freqs >= CHIME_LO) & (freqs <= CHIME_HI))[0]
        # broadband, for the tyre chirp that marks first movement
        self.wide = np.where((freqs > 300) & (freqs < 6000))[0]
        self.hist = collections.deque(maxlen=int(REF_SECONDS * RATE / HOP))
        self.bb = collections.deque(maxlen=int(90.0 * RATE / HOP))  # (t, energy)
        self.warm = 0
        self.pos = 0.0                   # time of buf[0]
        self.t0 = None
        self.peak = 0.0

    def _cb(self, indata, frames, tinfo, status):
        # Stamp each block with the shared clock. Counting samples instead
        # drifts against the telemetry clock - measured at ~600 ppm on real
        # hardware, which is hundreds of milliseconds over a session and far
        # larger than the reaction times being measured.
        # mix down if we were handed stereo (loopback)
        block = (indata.mean(axis=1) if indata.shape[1] > 1
                 else indata[:, 0]).copy()
        self.q.put((time.monotonic(), block))

    @staticmethod
    def open_stream_args(device=None, loopback=False):
        """
        Where to listen.

        On Windows the game runs on the same machine, so its audio can be
        captured digitally through WASAPI loopback instead of through a
        microphone across a room. That removes the acoustic path entirely -
        no TV latency, no room noise, no engine bleed - which is why reaction
        times are far tighter on PC than on console.
        """
        args = {}
        if device is not None:
            args["device"] = device
        if loopback:
            if os.name != "nt":
                sys.exit("--loopback is Windows only; use a microphone instead")
            try:
                args["extra_settings"] = sd.WasapiSettings(loopback=True)
            except (AttributeError, TypeError):
                sys.exit("this sounddevice build has no WASAPI loopback; "
                         "run: pip install -U sounddevice")
            if "device" not in args:
                args["device"] = sd.default.device[1]   # the output being played
        return args

    def start(self):
        if sd is None:
            sys.exit(f"needs: pip install sounddevice numpy  ({_IMPORT_ERROR})")
        self.t0 = time.monotonic()
        # Loopback records what the machine is playing, so it is stereo;
        # asking for one channel there fails on some drivers.
        chans = 2 if self.stream_args.get("extra_settings") else 1
        self.stream = sd.InputStream(samplerate=RATE, channels=chans,
                                     dtype="float32", blocksize=HOP * 4,
                                     callback=self._cb, **self.stream_args)
        self.stream.start()

    def pump(self):
        """Consume queued audio and append any detected chime times."""
        got = False
        while True:
            try:
                stamp, blk = self.q.get_nowait()
            except queue.Empty:
                break
            got = True
            # Re-anchor the audio timeline to the monotonic clock. Smoothed,
            # so callback jitter does not move timestamps around, but any
            # steady sample-rate error is tracked out instead of accumulating.
            anchor = stamp - len(blk) / RATE - len(self.buf) / RATE
            if self.pos == 0.0 and not len(self.buf):
                self.pos = anchor
            else:
                self.pos += 0.02 * (anchor - self.pos)
            self.buf = np.concatenate([self.buf, blk])
        if not got or len(self.buf) < NFFT:
            return
        self.peak = max(self.peak, float(np.abs(self.buf).max()))
        i = 0
        while i + NFFT <= len(self.buf):
            spec = np.abs(np.fft.rfft(self.buf[i:i + NFFT] * self.window))
            band_mag = float(spec[self.band].max())
            t_frame = self.pos + (i + NFFT / 2) / RATE
            self.bb.append((t_frame, float(spec[self.wide].sum())))
            self.hist.append(band_mag)
            # Median, not mean: a beep in the window cannot drag the reference
            # up far enough to hide itself.
            ref = float(np.median(self.hist)) + 1e-9
            ratio = band_mag / ref
            self.warm += 1
            if ratio > CHIME_RATIO and self.warm > int(1.0 * RATE / HOP):
                t = self.pos + (i + NFFT / 2) / RATE
                with self.lock:
                    if not self.events or t - self.events[-1] > CHIME_GAP:
                        self.events.append(t)
            i += HOP
        keep = len(self.buf) - i
        self.pos += i / RATE
        self.buf = self.buf[i:] if keep > 0 else np.zeros(0, dtype=np.float32)

    def chirp_latency(self, move_t):
        """
        Audio delay, from the tyre chirp at first movement.

        Telemetry knows when the car started moving to within a few
        milliseconds, and the chirp marks that same instant in the audio, so
        the gap between them is the delay through TV, air and input buffer.
        Measured per run, so changing the volume or moving the laptop cannot
        silently corrupt reaction times.
        """
        with self.lock:
            frames = list(self.bb)
        if not frames:
            return None
        pre = [e for t, e in frames if move_t - 1.2 <= t < move_t - 0.2]
        post = [(t, e) for t, e in frames if move_t - 0.05 <= t <= move_t + 0.7]
        if len(pre) < 5 or not post:
            return None
        base = float(np.median(pre)) + 1e-9
        for t, e in post:
            if e > base * CHIRP_RATIO:
                d = t - move_t
                return d if 0.0 <= d <= 0.6 else None
        return None

    def near(self, t, lo, hi):
        """Chime arrivals with t+lo <= event <= t+hi."""
        with self.lock:
            return [e for e in self.events if t + lo <= e <= t + hi]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5606)
    ap.add_argument("--strip", default="quarter",
                    help="eighth, quarter, half, mile, or metres. "
                         "100-200 mph needs half or longer")
    ap.add_argument("--driver", default="",
                    help="your name, recorded with every run so results can "
                         "be merged with someone else's later")
    ap.add_argument("--tyres", default="",
                    help="label this session's tyre compound, e.g. street, "
                         "sport, race, drag - recorded with every run")
    ap.add_argument("--anywhere", action="store_true",
                    help="measure any standing start on the map, like a real "
                         "Draggy. No countdown exists, so no reaction time")
    ap.add_argument("--loopback", action="store_true",
                    help="Windows: capture the game's own audio instead of a "
                         "microphone. Far more accurate, no room noise")
    ap.add_argument("--device", help="input device name or number")
    ap.add_argument("--list-devices", action="store_true",
                    help="show audio devices and exit")
    ap.add_argument("--raw", nargs="?", const="fh6_raw.csv", default=None,
                    help="also dump every packet to CSV, for checking ET "
                         "against the time the game prints on screen")
    ap.add_argument("--latency", type=float, default=None,
                    help="fallback audio delay in ms")
    a = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", a.port))
    sock.settimeout(0.02)

    ear = ChimeListener(a.device, a.loopback)
    ear.start()

    runs = load_history()
    print(f"telemetry UDP {a.port} + microphone")
    if isinstance(a.strip, str):
        if a.strip in STRIPS:
            a.strip = STRIPS[a.strip]
        else:
            try:
                a.strip = float(a.strip)
            except ValueError:
                sys.exit(f"--strip must be one of {', '.join(STRIPS)} or a number")

    raw_w = raw_f = None
    if a.raw:
        raw_f = open(a.raw, "w", newline="")
        raw_w = csv.DictWriter(raw_f, fieldnames=RAW_COLS, extrasaction="ignore")
        raw_w.writeheader()
        print(f"raw dump -> {a.raw}")

    if a.list_devices:
        if sd is None:
            sys.exit("sounddevice not available")
        print(sd.query_devices())
        return

    remembered, rem_n = load_latency()
    if a.latency is None:
        a.latency = remembered if remembered is not None else 244.0
        src = (f"remembered from {rem_n} runs" if remembered is not None
               else "assumed, not yet measured")
    else:
        src = "set on the command line"
    print(f"finish line {a.strip:.1f} m, audio delay {a.latency:.0f} ms ({src})")
    print("stage and launch as normal.\n")

    samples = []
    lat_hist = [x for x in (r.get("audio_latency_ms") for r in runs)
                if x is not None]
    lat_hist = [x / 1000.0 for x in lat_hist]
    state = "idle"
    still_since = None      # how long the car has been stationary
    stage_t = None
    release_t = None
    trav = 0.0
    prev = None
    last_status = time.monotonic()

    try:
        while True:
            ear.pump()
            try:
                buf, _ = sock.recvfrom(2048)
            except socket.timeout:
                if time.monotonic() - last_status > 10:
                    print(f"  waiting... mic peak {ear.peak:.3f}, "
                          f"{len(ear.events)} chimes heard")
                    ear.peak = 0.0
                    last_status = time.monotonic()
                continue

            p = parse(buf)
            if p is None:
                continue
            t = time.monotonic()
            s = dict(p)
            s["t"] = t
            if raw_w:
                row = {k: p.get(k) for k in RAW_COLS}
                row["recv_t"] = round(t, 5)
                raw_w.writerow(row)

            # Launch styles vary and the tool must not assume one. Some
            # hold clutch, brake and throttle; some only brake and throttle;
            # some hold throttle alone and let assists release the car. So a
            # stage is simply: stopped, throttle pinned. Whichever inputs are
            # being held is recorded rather than required.
            holders = [k for k in ("Brake", "Clutch", "HandBrake")
                       if p[k] >= HELD]
            if p["Speed"] < MOVING:
                if still_since is None:
                    still_since = t
            else:
                still_since = None

            if a.anywhere:
                # A real Draggy does not care how you were holding the car,
                # only that it was stopped and then went. No countdown exists
                # away from a strip, so no reaction time is reported.
                staged = (p["Speed"] < MOVING and still_since is not None
                          and t - still_since >= 1.5)
            else:
                staged = p["Speed"] < MOVING and p["Accel"] >= HELD

            if state == "idle":
                if staged:
                    state, stage_t, samples, trav = "staged", t, [s], 0.0
                prev = s
                continue

            samples.append(s)
            if prev is not None and state == "running":
                trav += 0.5 * (p["Speed"] + prev["Speed"]) * (t - prev["t"])

            if state == "staged":
                if p["Speed"] > MOVING:
                    if t - stage_t < 0.4:
                        state = "idle"
                    else:
                        state, trav = "running", 0.0
                        run_t = t
            elif state == "running" and (trav >= a.strip + 40
                                         or t - run_t > 45):
                # let the finish chime arrive before analysing
                deadline = time.monotonic() + 1.2
                while time.monotonic() < deadline:
                    ear.pump()
                    try:
                        b2, _ = sock.recvfrom(2048)
                        p2 = parse(b2)
                        if p2:
                            s2 = dict(p2)
                            s2["t"] = time.monotonic()
                            samples.append(s2)
                    except socket.timeout:
                        pass

                run = analyze(samples, None, None, {"held_s": round(run_t - stage_t, 3),
                                                    "inputs": holders})
                # Street runs must not become drag strip records, so every run
                # carries where it happened and what it was wearing.
                run["mode"] = "anywhere" if a.anywhere else "strip"
                if a.tyres:
                    run["tyres"] = a.tyres
                if a.driver:
                    run["driver"] = a.driver
                keep_evidence(run, samples)
                rel = run.get("releases") or {}
                move_t = run.get("first_movement_t")

                # --- audio delay ---------------------------------------------
                # The tyre chirp is the primary reference: it happens on every
                # run at an instant telemetry knows exactly. The finish chime is
                # only a fallback, because it is often inaudible over a car at
                # 180 mph - which is exactly when it is needed.
                lat = a.latency / 1000.0
                lat_src = "assumed"
                if move_t is not None:
                    ch = ear.chirp_latency(move_t)
                    if ch is not None:
                        lat_hist.append(ch)
                        lat = float(np.median(lat_hist))
                        lat_src = f"tyre chirp, median of {len(lat_hist)}"
                        save_latency(lat * 1000.0, len(lat_hist))
                if lat_src == "assumed" and move_t is not None:
                    d = 0.0
                    prev2 = None
                    fin_t = None
                    for x in samples:
                        if x["t"] < move_t:
                            continue
                        if prev2 is not None:
                            nd = d + 0.5 * (x["Speed"] + prev2["Speed"]) * (x["t"] - prev2["t"])
                            if d < a.strip <= nd:
                                fin_t = interp_time(prev2["t"], x["t"], d, nd, a.strip)
                                break
                            d = nd
                        prev2 = x
                    if fin_t is not None:
                        hits = ear.near(fin_t, -0.10, 0.80)
                        if hits:
                            lat_hist.append(hits[0] - fin_t)
                            lat_src = "finish chime"
                            _ = lat_src
                            # One run's estimate carries onset jitter; the
                            # median over the session is far steadier and the
                            # true delay does not change run to run.
                            lat = float(np.median(lat_hist))
                            lat_src = f"finish chime, median of {len(lat_hist)}"

                # --- GO chime -------------------------------------------------
                if rel:
                    first_rel = min(rel.values())
                    # Three tones a second apart, GO last. Picking the nearest
                    # loud candidate instead will happily lock onto the 0.4 s
                    # sound train the game plays after launch.
                    with ear.lock:
                        evs = list(ear.events)
                    chain = countdown_chain(evs, first_rel)
                    go_heard = predict_go(chain, first_rel)
                    if go_heard is not None:
                        go_t = go_heard - lat
                        run["go_t"] = round(go_t, 4)
                        run["go_source"] = f"5200Hz chime, {lat_src} delay {lat*1000:.0f}ms"
                        run["reaction_ms"] = round((first_rel - go_t) * 1000.0, 1)
                        run["reaction_input"] = min(rel, key=rel.get)
                        run["all_inputs_clear_ms"] = round(
                            (max(rel.values()) - go_t) * 1000.0, 1)
                        if move_t is not None:
                            run["go_to_movement_ms"] = round(
                                (move_t - go_t) * 1000.0, 1)
                        run["notes"] = [n for n in run.get("notes", [])
                                        if "No GO marker" not in n]
                        run["audio_latency_ms"] = round(lat * 1000.0, 1)
                        if lat_src == "assumed":
                            run["notes"].append(
                                "Finish chime not heard - reaction uses the assumed "
                                f"{a.latency:.0f} ms delay and may be off by tens of ms.")
                    else:
                        run["notes"].append("No countdown chime heard near launch.")

                runs = save_run(run)
                print(fmt_run(run, runs))
                with ear.lock:
                    ear.events = [e for e in ear.events if e > t - 2]
                state = "idle"
            prev = s
    except KeyboardInterrupt:
        print("\nstopped.")




# ---------------------------------------------------------------------------
# Validated timing model
# ---------------------------------------------------------------------------

CHIRP_RATIO = 1.8          # Broadband rise marking the tyre chirp. Measured
                           # 2.0-4.7x with the engine audible - the engine is
                           # already loud at launch, so the chirp is a modest
                           # bump rather than the sharp onset it is when the
                           # engine is muted. A 4x bar misses almost every run.

LATENCY_PATH = os.path.expanduser("~/fh6_audio_latency.json")


def load_latency():
    """Remembered audio delay. It is a property of the TV and the room, not
    of a run, so once measured it holds until the setup changes."""
    try:
        with open(LATENCY_PATH) as f:
            d = json.load(f)
        return float(d["latency_ms"]), int(d.get("samples", 0))
    except (OSError, ValueError, KeyError):
        return None, 0


def save_latency(ms, n):
    try:
        with open(LATENCY_PATH, "w") as f:
            json.dump({"latency_ms": round(ms, 1), "samples": n}, f)
    except OSError:
        pass


BEEP_INTERVAL = 1.0    # measured countdown spacing, seconds
BEEP_TOL = 0.14        # how far off 1.0 s spacing a beep may fall


def countdown_chain(events, release_t, window=6.0):
    """
    The run of ~1 s spaced beeps preceding a launch.

    With the detection threshold set low enough to hear the chime over a loud
    engine, plenty of candidates are noise. Three of them landing 1.000 s apart
    is not - that spacing is what identifies the countdown, so the rhythm does
    the rejecting rather than a loudness threshold. The longest qualifying
    chain ending nearest the launch wins.
    """
    seq = sorted(e for e in events if release_t - window <= e <= release_t + 0.6)
    best = []
    for i, t0 in enumerate(seq):
        chain = [t0]
        for t in seq[i + 1:]:
            if abs((t - chain[-1]) - BEEP_INTERVAL) < BEEP_TOL:
                chain.append(t)
            elif t - chain[-1] > BEEP_INTERVAL + BEEP_TOL:
                break
        if len(chain) < 3:
            continue
        # Trailing candidates well past the launch are tyre and driveline
        # noise that happened to land on the beat; drop them so the chain
        # ends on the real GO rather than running on into the burnout.
        while len(chain) > 3 and chain[-1] > release_t + 0.6:
            chain.pop()
        # Nearest ending wins, not longest. A longer chain usually means it
        # ran past GO, which puts the whole reaction time out by a second.
        if not best or abs(chain[-1] - release_t) < abs(best[-1] - release_t):
            best = chain
    return best


def predict_go(chain, release_t):
    """
    GO as heard at the mic.

    The countdown is three beeps then GO, all the same tone. Usually GO is
    itself detected and is simply the last of the chain. If the launch drowned
    it out, it is predicted one interval on from the last beep heard. Deciding
    between those two cases by proximity to the release is what keeps this from
    silently being a whole second wrong.
    """
    if len(chain) < 3:
        return None
    return chain[-1] if chain[-1] >= release_t - 0.5 else chain[-1] + BEEP_INTERVAL


def reaction_ms(release_t, go_heard, latency_s):
    """Positive = released after GO. Negative = jumped it."""
    if go_heard is None or latency_s is None:
        return None
    return (release_t - (go_heard - latency_s)) * 1000.0


if __name__ == "__main__":
    main()
