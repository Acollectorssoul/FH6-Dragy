#!/usr/bin/env python3
"""
fh6_slip.py - your runs as a track time slip and as a performance report.

    python3 fh6_slip.py                 latest run, both views
    python3 fh6_slip.py --run 3
    python3 fh6_slip.py --all
    python3 fh6_slip.py --style slip    just the time slip
    python3 fh6_slip.py --style report  just the performance report
    python3 fh6_slip.py --event "GOLIATH DRAG" --open

The slip follows the CompuLink StarTRAK layout used at real tracks, two lanes
and all. The right lane is your personal best, so every run is against your own
quickest - which is also what makes the holeshot line mean something.

The report follows the Draggy layout: the run as speed and acceleration curves,
headline figures, then splits every 10 mph.
"""

import argparse
import html
import json
import os
import subprocess
import sys

HISTORY = os.path.expanduser("~/fh6_draggy_runs.json")

try:
    from fh6_cars import car_name
except ImportError:
    def car_name(ordinal, names=None):
        return f"Car {ordinal}" if ordinal else "---"

_I = ('<svg viewBox="0 0 24 24" width="17" height="17" fill="none" '
      'stroke="#7d8b99" stroke-width="1.7" stroke-linecap="round">')
ICON_CLOCK = _I + '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>'
ICON_GAUGE = (_I + '<path d="M4 16a8 8 0 1 1 16 0"/><path d="M12 16l4.5-4.5"/>'
              '</svg>')
ICON_RULER = (_I + '<rect x="3" y="9" width="18" height="6" rx="1"/>'
              '<path d="M7 9v3M11 9v3M15 9v3M19 9v3"/></svg>')
OUT = os.path.expanduser("~/fh6_slip.html")

CSS = """
:root{
  --paper:#f7f6f2; --slip-ink:#1a1c22; --slip-dim:#6d7280;
  --blue:#2a93e8; --card:#fff; --ink:#16202b; --dim:#7d8b99; --line:#e4eaf0;
}
*{box-sizing:border-box}
body{margin:0;padding:34px 18px 70px;background:#101418;
     font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     display:flex;flex-wrap:wrap;gap:30px;justify-content:center;align-items:flex-start}
.slip{width:334px;background:var(--paper);color:var(--slip-ink);
      font-family:"DejaVu Sans Mono","SF Mono",Menlo,monospace;font-size:13px;
      padding:22px 24px 26px;box-shadow:0 12px 30px rgba(0,0,0,.5);
      line-height:1.6;position:relative}
.slip::after{content:"";position:absolute;left:0;right:0;bottom:-9px;height:10px;
  background:repeating-linear-gradient(-45deg,var(--paper) 0 7px,transparent 7px 14px)}
.slip h1{font-size:15px;margin:0;text-align:center;letter-spacing:.03em;font-weight:700}
.slip .sub{text-align:center;font-size:11px;color:var(--slip-dim);line-height:1.55}
.rule{border-top:1px dashed #b6bac2;margin:11px 0}
.grid{display:grid;grid-template-columns:1fr 82px 82px;font-size:12.5px}
.grid>div{padding:1px 0}
.grid .h{text-align:center;font-weight:700;font-size:10.5px;letter-spacing:.09em}
.grid .v{text-align:right;font-variant-numeric:tabular-nums}
.grid .b{font-weight:700;font-size:15px}
.foot{font-size:10.5px;color:var(--slip-dim);line-height:1.62;margin-top:10px}
/* pinned around the board: smaller by design, not by transform */
.notrace{display:flex;align-items:center;justify-content:center;
  color:#9aa7b4;font-size:11.5px;text-align:center;padding:0 20px}
.slip.mini{width:168px;padding:11px 12px 13px;font-size:7.5px;line-height:1.45;
  cursor:pointer}
.slip.mini h1{font-size:8.5px;letter-spacing:.01em}
.slip.mini .sub{font-size:6.5px;line-height:1.4}
.slip.mini .rule{margin:5px 0}
.slip.mini .grid{font-size:7.5px;grid-template-columns:1fr 40px 40px}
.slip.mini .grid .h{font-size:6px}
.slip.mini .grid .b{font-size:9px}
.slip.mini .foot{font-size:6px;line-height:1.45;margin-top:5px}
.slip.mini::after{height:7px}
.slip.med{width:250px;padding:15px 17px 17px;font-size:10.5px;line-height:1.5}
.slip.med h1{font-size:11.5px}
.slip.med .sub{font-size:8.5px}
.slip.med .grid{font-size:10px;grid-template-columns:1fr 58px 58px}
.slip.med .grid .h{font-size:8px}
.slip.med .grid .b{font-size:12.5px}
.slip.med .foot{font-size:8px;margin-top:7px}
.rep{width:352px;background:var(--blue);border-radius:20px;padding:14px 14px 18px;
     box-shadow:0 12px 30px rgba(0,0,0,.5)}
.rep .top{color:#fff;text-align:center;font-size:12px;padding:2px 0 11px}
.pill{display:inline-block;background:rgba(255,255,255,.22);color:#fff;
      border-radius:20px;padding:3px 12px;font-size:11px;margin-bottom:7px}
.sheet{background:var(--card);border-radius:14px;overflow:hidden}
.hd{padding:13px 16px 2px;position:relative}
.hd h2{margin:0;color:var(--blue);font-size:15px;font-weight:600;text-align:center}
.stamp{position:absolute;right:12px;top:6px;border:2px solid #d24b3e;color:#d24b3e;
  border-radius:50%;width:50px;height:50px;display:flex;align-items:center;
  justify-content:center;font-size:9px;font-weight:700;transform:rotate(-14deg);
  opacity:.85;line-height:1.1}
.legend{display:flex;gap:14px;justify-content:center;font-size:10.5px;
        color:var(--dim);padding:8px 0 0}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px}
.chart{padding:2px 8px 4px}
.trio{display:grid;grid-template-columns:repeat(3,1fr);background:#f4f8fb;
      border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.trio>div{padding:11px 4px 13px;text-align:center}
.ico{margin-bottom:2px;line-height:0}
.bul{display:inline-block;width:6px;height:6px;border-radius:50%;
  background:#2a93e8;margin-right:8px;vertical-align:middle}
.trio>div+div{border-left:1px solid var(--line)}
.trio .n{font-size:19px;font-weight:600;color:var(--ink)}
.trio .k{font-size:10px;color:var(--dim);margin-top:3px}
.splits{padding:9px 16px 15px;display:grid;grid-template-columns:1fr 1fr;
        column-gap:18px;font-size:12.5px}
.row{display:flex;justify-content:space-between;padding:6.5px 0;
     border-bottom:1px solid var(--line);color:var(--ink)}
.row span{color:var(--dim)}
.row b{font-weight:600;font-variant-numeric:tabular-nums}
@media print{body{background:#fff}}
"""


def g(run, *path):
    cur = run
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur if isinstance(cur, (int, float, str)) else None


def num(v, fmt, dash="---"):
    return format(v, fmt) if isinstance(v, (int, float)) else dash


def pi_class(pi):
    """
    Forza class letter for a performance index.

    FH6 added the R class above S2, which shifts every boundary down 100
    from the FH3-FH5 ladder (there, S1 was 801-900). Confirmed against the
    game: a PI 799 car shows as S1 in FH6. So: D <=400, C 401-500, B 501-600,
    A 601-700, S1 701-800, S2 801-900, R 901-998, X 999.
    """
    if not isinstance(pi, (int, float)) or pi <= 0:
        return "---"
    for hi, name in ((400, "D"), (500, "C"), (600, "B"), (700, "A"),
                     (800, "S1"), (900, "S2"), (998, "R")):
        if pi <= hi:
            return f"{name} {int(pi)}"
    return f"X {int(pi)}"


def best_run(runs, like=None):
    """
    Quickest quarter mile - in the SAME car, when one is given.

    A personal best set in a different car is not a comparison, it is just the
    faster car. Runs are matched on the Forza car ordinal, falling back to all
    runs only when the car was never recorded.
    """
    pool = runs
    if like is not None:
        ordinal = like.get("car_ordinal")
        if ordinal:
            same = [r for r in runs if r.get("car_ordinal") == ordinal]
            if same:
                pool = same
    scored = [(v, i) for i, r in enumerate(pool)
              if not r.get("foul")
              and isinstance((v := g(r, "et", "1/4mi", "s")), (int, float))]
    return pool[min(scored)[1]] if scored else None


def slip_html(run, pb, event, index, total, compact=False, size=""):
    same_car = bool(pb and run.get("car_ordinal")
                    and pb.get("car_ordinal") == run.get("car_ordinal"))
    def row(label, path, fmt, big=False):
        a, b = g(run, *path), (g(pb, *path) if pb else None)
        cls = ' class="v b"' if big else ' class="v"'
        return (f'<div>{html.escape(label)}</div>'
                f'<div{cls}>{num(a, fmt)}</div><div{cls}>{num(b, fmt)}</div>')

    rt_a, rt_b = g(run, "reaction_ms"), (g(pb, "reaction_ms") if pb else None)
    et_a, et_b = g(run, "et", "1/4mi", "s"), (g(pb, "et", "1/4mi", "s") if pb else None)
    hole = "---"
    if run.get("foul"):
        hole = "VOID"
    elif all(isinstance(x, (int, float)) for x in (rt_a, rt_b, et_a, et_b)):
        hole = "YES" if (rt_a < rt_b) != (et_a < et_b) else "NO"

    rows = (
        f'<div>CLASS</div><div class="v">{pi_class(g(run,"car_pi"))}</div>'
        f'<div class="v">{pi_class(g(pb,"car_pi")) if pb else "---"}</div>'
        f'<div>DIAL</div><div class="v">---</div><div class="v">---</div>'
        f'<div>R/T</div>'
        f'<div class="v"{" style=\'color:#c0392b;font-weight:700\'" if run.get("foul") else ""}>'
        f'{num(rt_a/1000 if isinstance(rt_a,(int,float)) else None, ".3f")}'
        f'{" RED" if run.get("foul") else ""}</div>'
        f'<div class="v">{num(rt_b/1000 if isinstance(rt_b,(int,float)) else None, ".3f")}</div>'
        + row("60'", ("et", "60ft", "s"), ".3f")
        + row("330", ("et", "330ft", "s"), ".3f")
        + row("1/8", ("et", "1/8mi", "s"), ".3f")
        + row("MPH", ("et", "1/8mi", "mph"), ".2f")
        + row("1000", ("et", "1000ft", "s"), ".3f")
        + row("1/4", ("et", "1/4mi", "s"), ".3f", big=True)
        + row("MPH", ("et", "1/4mi", "mph"), ".2f"))

    when = (run.get("timestamp") or "").replace("T", "  ")
    cls = " mini" if compact else (" " + size if size else "")
    return f"""
<div class="slip{cls}">
  <h1>{html.escape(event)}</h1>
  <div class="sub">FORZA HORIZON 6<br>{html.escape(when)}</div>
  <div class="rule"></div>
  <div class="grid"><div></div><div class="h">THIS RUN</div>
    <div class="h">{'BEST' if same_car else 'BEST*'}</div></div>
  <div class="rule"></div>
  <div class="grid">{rows}</div>
  <div class="rule"></div>
  <div class="foot">
    RUN # {index} OF {total}<br>
    {"<b>RED LIGHT - RUN VOID</b><br>" if run.get("foul") else ""}
    HOLESHOT: {hole}<br>
    {html.escape(str(g(run,'drivetrain') or ''))} &middot;
    {num(g(run,'peak_power_hp'), '.0f')} HP &middot;
    TYRE {num(g(run,'tire_temp'), '.0f')} &middot;
    LAUNCH {num(g(run,'launch_rpm'), '.0f')} RPM<br>
    {html.escape(car_name(g(run,'car_ordinal')))}
    {'' if same_car else '&middot; *BEST IS ANOTHER CAR'}<br><br>
    ..... FH6 DRAGGY
  </div>
</div>"""


def chart(run, w=320, h=176):
    """
    Speed and acceleration against time, with real axes.

    Two unlabelled lines tell you the shape and nothing else. Scales on both
    sides make the numbers readable, and ticks on the curve mark where each
    distance split actually fell - so you can see the 60ft and the traps
    sitting on the trace rather than only in the table below.
    """
    trace = run.get("trace") or []
    if len(trace) < 3:
        return f'<div style="height:{h}px"></div>'
    ts = [p[0] for p in trace]
    sp = [p[1] for p in trace]
    ac = [p[2] if len(p) > 2 else 0.0 for p in trace]
    t0, t1 = min(ts), max(ts)
    span = (t1 - t0) or 1.0

    # Axis tops chosen so the four divisions land on round numbers - a scale
    # reading 190/142/95 is technically correct and useless to read.
    smax = max(sp) or 1
    s_top = next((x for x in (20, 40, 60, 80, 100, 120, 160, 200, 240,
                              300, 400, 500) if x >= smax), 500)
    a_lim = max(1.0, max(abs(x) for x in ac))
    a_top = next((x for x in (1.0, 1.5, 2.0, 3.0, 4.0, 6.0) if x >= a_lim), 6.0)

    L, R, T, B = 30, 28, 10, 20
    pw, ph = w - L - R, h - T - B

    def X(t):
        return L + (t - t0) / span * pw

    def Ys(v):
        return T + ph - (v / s_top) * ph

    def Ya(v):
        return T + ph / 2 - (v / a_top) * (ph / 2)

    parts = []
    # horizontal grid with speed on the left, g on the right
    n = 4
    for i in range(n + 1):
        y = T + ph * i / n
        parts.append(f'<line x1="{L}" x2="{L+pw}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'stroke="#e8eef4"/>')
        parts.append(f'<text x="{L-5}" y="{y+3.5:.1f}" text-anchor="end" '
                     f'font-size="9" fill="#8b98a6">{s_top*(n-i)/n:.0f}</text>')
    for mult, val in ((1, a_top), (0, 0.0), (-1, -a_top)):
        y = Ya(val)
        parts.append(f'<text x="{L+pw+5}" y="{y+3.5:.1f}" font-size="9" '
                     f'fill="#e0a25c">{val:+.1f}</text>')
    # time ticks: aim for four to six labels, not one every half second
    tick = next((st for st in (0.5, 1, 2, 2.5, 5, 10, 20)
                 if span / st <= 6), 20)
    k = 0.0
    while k <= t1 + 1e-6:
        if k >= t0:
            parts.append(f'<text x="{X(k):.1f}" y="{h-6}" text-anchor="middle" '
                         f'font-size="9" fill="#8b98a6">{k:g}s</text>')
        k += tick
    # split markers on the speed curve
    for key, label in (("60ft", "60ft"), ("1/8mi", "1/8"), ("1/4mi", "1/4"),
                       ("1/2mi", "1/2"), ("1mi", "1mi")):
        e = (run.get("et") or {}).get(key) or {}
        if not isinstance(e.get("s"), (int, float)) or not (t0 <= e["s"] <= t1):
            continue
        x = X(e["s"])
        v = e.get("mph")
        y = Ys(v) if isinstance(v, (int, float)) else T + ph
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y:.1f}" y2="{T+ph}" '
                     f'stroke="#c8d6e2" stroke-dasharray="2 2"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="#2a93e8"/>')
        parts.append(f'<text x="{x:.1f}" y="{T+ph-4:.1f}" text-anchor="middle" '
                     f'font-size="8" fill="#7d8b99">{label}</text>')

    accel = " ".join(f"{X(t):.1f},{Ya(v):.1f}" for t, v in zip(ts, ac))
    speed = " ".join(f"{X(t):.1f},{Ys(v):.1f}" for t, v in zip(ts, sp))
    parts.append(f'<polyline points="{accel}" fill="none" stroke="#f2932c" '
                 f'stroke-width="1.3" opacity=".9"/>')
    parts.append(f'<polyline points="{speed}" fill="none" stroke="#2a93e8" '
                 f'stroke-width="2.2"/>')
    parts.append(f'<line x1="{L}" x2="{L+pw}" y1="{T+ph}" y2="{T+ph}" stroke="#cfdae4"/>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">'
            + "".join(parts) + "</svg>")


def report_html(run, event, embed=False):
    """
    embed=True drops the outer blue frame and folds the splits into a
    disclosure, for use inside a page that is already blue and where the
    headline figures should be the thing in plain sight.
    """
    acc, et = run.get("accel") or {}, run.get("et") or {}
    # Everything below 0-60 is still recorded, it is just noise on a card this
    # size - twelve near-identical rows crowd out the numbers people actually
    # compare. The full set is in the run history whenever a bigger layout
    # wants it.
    items = [(k, f"{acc[k]['s']:.2f}s") for k in ("0-60", "0-100", "0-150")
             if isinstance((acc.get(k) or {}).get("s"), (int, float))]
    for k in ("60-130", "100-150", "100-200"):
        v = g(run, "roll", k)
        if isinstance(v, (int, float)):
            items.append((f"{k}mph", f"{v:.2f}s"))
    for key, label in (("60ft", "60ft"), ("330ft", "330ft"), ("1/8mi", "1/8"),
                       ("1000ft", "1000ft"), ("1/4mi", "1/4")):
        e = et.get(key) or {}
        if isinstance(e.get("s"), (int, float)):
            txt = f"{e['s']:.2f}s"
            if isinstance(e.get("mph"), (int, float)):
                txt += f"@{e['mph']:.2f}"
            items.append((label, txt))
    half = (len(items) + 1) // 2

    def col(pairs):
        # The leading blue dot is the detail that makes this read as a
        # Draggy report rather than a generic two-column table.
        return "".join(f'<div class="row"><span><i class="bul"></i>'
                       f'{html.escape(k)}</span>'
                       f'<b>{html.escape(v)}</b></div>' for k, v in pairs)

    sheet = f"""
    <div class="hd"><h2>Performance Report</h2><div class="stamp">VALID</div></div>
    <div class="legend">
      <span><span class="dot" style="background:#2a93e8"></span>Speed (mph)</span>
      <span><span class="dot" style="background:#f2932c"></span>Acceleration (g)</span>
    </div>
    <div class="chart">{chart(run)}</div>
    <div class="trio">
      <div><div class="ico">{ICON_CLOCK}</div>
           <div class="n">{num(g(run,'et','1/4mi','s'), '.2f')}s</div>
           <div class="k">1/4 mile</div></div>
      <div><div class="ico">{ICON_GAUGE}</div>
           <div class="n">{num(g(run,'et','1/4mi','mph'), '.1f')}</div>
           <div class="k">Trap mph</div></div>
      <div><div class="ico">{ICON_RULER}</div>
           <div class="n">{num(g(run,'et','60ft','s'), '.2f')}s</div>
           <div class="k">60 ft</div></div>
    </div>"""

    splits = (f'<div class="splits"><div>{col(items[:half])}</div>'
              f'<div>{col(items[half:])}</div></div>')
    if embed:
        rt = g(run, "reaction_ms")
        rt_txt = ("---" if not isinstance(rt, (int, float))
                  else f"{rt/1000:+.3f}" + (" RED" if run.get("foul") else ""))
        when = html.escape((run.get("timestamp") or "").replace("T", "  "))
        return (f'<div class="sheet"><h2>{html.escape(event)}</h2>'
                f'<div class="when">{when} &middot; R/T {rt_txt}</div>'
                + sheet + splits + "</div>")

    return f"""
<div class="rep">
  <div class="top"><span class="pill">{html.escape(event)}</span><br>
    {html.escape((run.get('timestamp') or '').replace('T','  '))}</div>
  <div class="sheet">{sheet}{splits}</div>
</div>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--style", choices=("slip", "report", "both"), default="both")
    ap.add_argument("--event", default="HORIZON FESTIVAL DRAG STRIP")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(HISTORY):
        sys.exit(f"no run history at {HISTORY} - drive some runs first")
    runs = json.load(open(HISTORY))
    if not runs:
        sys.exit("run history is empty")
    if a.all:
        picks = list(range(len(runs) - 1, -1, -1))
    elif a.run:
        if not 1 <= a.run <= len(runs):
            sys.exit(f"run {a.run} out of range (1..{len(runs)})")
        picks = [a.run - 1]
    else:
        picks = [len(runs) - 1]

    body = ""
    for i in picks:
        if a.style in ("slip", "both"):
            pb = best_run(runs, runs[i])
            body += slip_html(runs[i], pb, a.event, i + 1, len(runs))
        if a.style in ("report", "both"):
            body += report_html(runs[i], a.event)

    open(OUT, "w").write("<!doctype html><meta charset=utf-8><title>FH6 results</title>"
                         f"<style>{CSS}</style>{body}")
    print(f"wrote {OUT}")
    if a.open:
        # "open" is macOS only
        if sys.platform == "darwin":
            subprocess.run(["open", OUT], check=False)
        elif os.name == "nt":
            os.startfile(OUT)              # noqa: S606 - Windows only
        else:
            subprocess.run(["xdg-open", OUT], check=False)


if __name__ == "__main__":
    main()
