#!/usr/bin/env python3
"""
fh6_web.py - browse your runs in a browser instead of a terminal.

    cd ~/fh6 && venv/bin/python3 fh6_web.py
    then open http://localhost:8760

Run list, personal bests, trends over time, side-by-side comparison, and the
time slip and performance report for any run. Reads the same history file the
tools write, and re-reads it on every request - so leave it open while you
drive and new runs appear as you refresh.

Nothing leaves the machine. The server binds to localhost only, so it is not
reachable from the rest of the network.
"""

import argparse
import http.server
import json
import os
import socketserver
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import fh6_slip as S
    from fh6_cars import car_name, load_names
except ImportError:
    sys.exit("needs fh6_slip.py and fh6_cars.py in the same folder")

HISTORY = os.path.expanduser("~/fh6_draggy_runs.json")   # overridable by --runs
PORT = 8760


def load():
    try:
        with open(HISTORY) as f:
            runs = json.load(f)
        return runs if isinstance(runs, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def scope_css(css, sel):
    """
    Confine a stylesheet to one container.

    Injecting the slip's stylesheet unscoped puts its .sheet, .trio and .row
    rules in the global cascade, where they silently override the page's own
    - with variables that are no longer defined, so cards turn transparent.
    Prefixing every selector keeps it to the element it was written for.
    """
    out = []
    for chunk in css.split("}"):
        if "{" not in chunk:
            continue
        head, body = chunk.split("{", 1)
        head = head.strip()
        if not head or head.startswith("@"):
            continue
        if head == ":root":
            out.append(f"{sel}{{{body}}}")
            continue
        parts = []
        for one in head.split(","):
            one = one.strip()
            if not one or one == "body":
                continue
            parts.append(one if one.startswith(sel) else f"{sel} {one}")
        if parts:
            out.append(f"{','.join(parts)}{{{body}}}")
    return "\n".join(out)


def q(run, *path):
    return S.g(run, *path)


def class_of(run):
    """Forza class letter for grouping - S2, X and so on."""
    txt = S.pi_class(run.get("car_pi"))
    return txt.split()[0] if txt and txt != "---" else "?"


def match(run, scope, value):
    if not value or value == "all":
        return True
    if scope == "car":
        return str(run.get("car_ordinal")) == str(value)
    return class_of(run) == value


def summary(runs):
    _names = load_names()
    out = []
    for i, r in enumerate(runs):
        out.append({
            "n": i + 1,
            "when": (r.get("timestamp") or "")[:19].replace("T", " "),
            "car": r.get("car_ordinal") or 0,
            "pi": r.get("car_pi") or 0,
            "cls": S.pi_class(r.get("car_pi")),
            "cl": class_of(r),
            "mode": r.get("mode", "strip"),
            "tyres": r.get("tyres") or "",
            "driver": r.get("driver") or "",
            "carname": car_name(r.get("car_ordinal"), _names),
            "rt": q(r, "reaction_ms"),
            "foul": bool(r.get("foul")),
            "sixty": q(r, "et", "60ft", "s"),
            "eighth": q(r, "et", "1/8mi", "s"),
            "quarter": q(r, "et", "1/4mi", "s"),
            "trap": q(r, "et", "1/4mi", "mph"),
            "s60": q(r, "accel", "0-60", "s"),
            "roll": q(r, "roll", "100-150"),
            "temp": q(r, "tire_temp"),
            "rpm": q(r, "launch_rpm"),
            "spin": q(r, "peak_wheelspin"),
        })
    return out


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FH6 Draggy</title><style>
:root{--blue:#1b7fd4;--blue-d:#14639f;--ink:#16202b;--dim:#66727f;
      --line:#dfe5ec;--bg:#eef2f6;--good:#1f8a52;--bad:#c0392b;--amber:#c47a10}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);font-size:14px;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  padding-bottom:70px;
  /* cork: a warm base with flecks at three scales, so it reads as board
     rather than as a flat brown rectangle */
  background-color:#c5a06c;
  background-image:
    radial-gradient(circle at 17% 31%, rgba(88,56,24,.13) 0 1.3px, transparent 1.4px),
    radial-gradient(circle at 61% 12%, rgba(88,56,24,.10) 0 1px,   transparent 1.1px),
    radial-gradient(circle at 34% 77%, rgba(122,84,42,.11) 0 1.6px, transparent 1.7px),
    radial-gradient(circle at 82% 54%, rgba(66,40,16,.09) 0 1.1px, transparent 1.2px),
    radial-gradient(circle at 8% 63%,  rgba(150,110,62,.13) 0 2px,  transparent 2.1px),
    radial-gradient(circle at 47% 44%, rgba(168,126,74,.11) 0 2.4px, transparent 2.5px),
    radial-gradient(circle at 73% 86%, rgba(96,62,28,.10) 0 1.8px, transparent 1.9px),
    radial-gradient(circle at 26% 51%, rgba(180,140,86,.12) 0 3px, transparent 3.1px),
    radial-gradient(ellipse at 50% 40%, rgba(255,236,205,.10), transparent 62%),
    linear-gradient(115deg, rgba(255,255,255,.05), rgba(0,0,0,.06));
  background-size:19px 23px,31px 29px,41px 43px,13px 15px,59px 61px,73px 71px,
    47px 53px,67px 63px,100% 100%,100% 100%;
  box-shadow:inset 0 0 160px rgba(60,34,10,.34)}

/* a run pinned to the board */
.board{display:grid;grid-template-columns:1fr minmax(0,560px) 1fr;
  gap:14px;align-items:start;justify-items:center}
/* the sides scroll on their own so the page itself never does */
.col{display:flex;flex-direction:column;align-items:center;gap:6px;width:100%}
.mid{width:100%;display:flex;justify-content:center}
@media (max-width:1180px){
  .board{grid-template-columns:1fr;height:auto}
  /* phone: the current run comes first, the pinned history after */
  .mid{order:-1}
  .col{flex-direction:row;flex-wrap:wrap;justify-content:center;gap:8px;
       height:auto;overflow:visible}
}
.tack{position:relative;margin:26px 0 0;width:max-content;
  filter:drop-shadow(0 10px 14px rgba(60,35,10,.42))}
.tack:nth-of-type(4n+1){transform:rotate(-1.6deg)}
.tack:nth-of-type(4n+2){transform:rotate(1.2deg)}
.tack:nth-of-type(4n+3){transform:rotate(-.6deg)}
.tack:nth-of-type(4n){transform:rotate(2deg)}
.tack.mini{margin-top:22px}
.tack.mini .pin{width:15px;height:15px;top:-8px}
.tack.mini .pin::after{top:10px;height:6px}
.tack.mini:hover{filter:drop-shadow(0 12px 16px rgba(60,35,10,.5));z-index:2}
.pin{position:absolute;top:-11px;left:50%;transform:translateX(-50%);
  width:20px;height:20px;border-radius:50%;z-index:3;
  background:radial-gradient(circle at 34% 30%, #ff8a80 0 18%, #e03b2f 55%, #8e1b12 100%);
  box-shadow:0 3px 5px rgba(50,20,5,.5), inset 0 -2px 3px rgba(0,0,0,.3)}
.pin::after{content:"";position:absolute;left:50%;top:14px;width:2px;height:7px;
  transform:translateX(-50%);background:linear-gradient(#9aa0a6,#5c6166);
  border-radius:0 0 1px 1px}
header{display:flex;align-items:center;gap:14px;padding:13px 20px;
  flex-wrap:wrap;background:var(--blue);color:#fff;
  box-shadow:0 1px 6px rgba(0,0,0,.18)}
.bar{max-width:960px;margin:0 auto;width:100%;display:flex;
  align-items:center;gap:14px;flex-wrap:wrap}
h1{margin:0;font-size:16px;font-weight:600;letter-spacing:.02em}
.sub{color:rgba(255,255,255,.8);font-size:12px}
select,button{background:rgba(255,255,255,.2);color:#fff;border:0;
  border-radius:15px;padding:6px 13px;font:inherit;font-size:12px}
button{cursor:pointer}
button.on{background:#fff;color:var(--blue-d);font-weight:600}
main{max-width:1560px;margin:0 auto;padding:16px 20px}
/* the board sits in a timber frame */
#pane-run .frame{border:11px solid #7d5326;border-radius:6px;
  box-shadow:inset 0 0 0 2px rgba(255,255,255,.10),
             inset 0 0 30px rgba(60,34,10,.30), 0 6px 18px rgba(0,0,0,.30);
  /* border only - a background here would paint over the cork */
  padding:16px 14px 22px}

.sheet{background:#fff;border:1px solid var(--line);border-radius:18px;
  overflow:hidden;margin:0 auto;max-width:560px;
  box-shadow:0 10px 28px rgba(60,35,10,.4)}
.sheet h2{margin:0;padding:15px 18px 3px;color:var(--blue);font-size:15px;
  font-weight:600;text-align:center}
.sheet .when{text-align:center;color:var(--dim);font-size:11.5px;padding-bottom:8px}
.hd{padding:12px 16px 2px;position:relative}
.hd h2{margin:0;color:var(--blue);font-size:14px;font-weight:600;text-align:center}
.stamp{position:absolute;right:14px;top:6px;border:2px solid #c0392b;color:#c0392b;
  border-radius:50%;width:46px;height:46px;display:flex;align-items:center;
  justify-content:center;font-size:8.5px;font-weight:700;transform:rotate(-14deg);
  opacity:.8;line-height:1.1}
.legend{display:flex;gap:14px;justify-content:center;font-size:10.5px;
  color:var(--dim);padding:8px 0 0}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px}
.chart{padding:2px 8px 4px}
.trio{display:grid;grid-template-columns:repeat(3,1fr);background:#f6f9fc;
  border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.trio>div{padding:11px 5px 13px;text-align:center}
.ico{margin-bottom:2px;line-height:0}
.bul{display:inline-block;width:6px;height:6px;border-radius:50%;
  background:var(--blue);margin-right:8px;vertical-align:middle}
.trio>div+div{border-left:1px solid var(--line)}
.trio .n{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums}
.trio .k{font-size:10.5px;color:var(--dim);margin-top:4px}
.more>summary{cursor:pointer;list-style:none;padding:12px 18px;color:var(--blue);
  font-size:12.5px;font-weight:600;text-align:center}
.more>summary::-webkit-details-marker{display:none}
.more>summary::after{content:" ▾"}
.more[open]>summary::after{content:" ▴"}
.splits{padding:10px 20px 16px;display:grid;grid-template-columns:1fr 1fr;
  column-gap:24px;font-size:13px}
.row{display:flex;justify-content:space-between;padding:7px 0;
  border-bottom:1px solid var(--line)}
.row span{color:var(--dim)} .row b{font-variant-numeric:tabular-nums}
.tabs{display:flex;gap:2px;margin-left:auto}
.tab{background:transparent;border:0;color:rgba(255,255,255,.72);
  border-radius:0;padding:8px 15px 9px;font-size:12.5px;font-weight:600;
  border-bottom:2.5px solid transparent;cursor:pointer;letter-spacing:.02em}
.tab.sel{color:#fff;border-bottom-color:#fff}
.pane{display:none} .pane.show{display:block}
.hintbox{color:#54381a;font-size:12px;margin:14px 2px;
  text-shadow:0 1px 0 rgba(255,255,255,.18)}
.fold{margin-top:6px}
.fold>summary{cursor:pointer;list-style:none;color:#54381a;font-size:11px;
  font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  padding:8px 2px;text-shadow:0 1px 0 rgba(255,255,255,.18)}
.fold>summary::-webkit-details-marker{display:none}
.fold>summary::after{content:" ▸"}
.fold[open]>summary::after{content:" ▾"}
.sec{font-size:11px;font-weight:700;letter-spacing:.12em;
  text-transform:uppercase;margin:26px 2px 10px;color:#54381a;
  text-shadow:0 1px 0 rgba(255,255,255,.18)}
.hist{background:#fff;border:1px solid var(--line);border-radius:12px;
  overflow:hidden;box-shadow:0 8px 20px rgba(60,35,10,.3)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{font-size:10px;color:var(--dim);font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;padding:11px 10px;text-align:right;background:#f6f9fc;
  border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
td{padding:11px 10px;text-align:right;border-bottom:1px solid var(--line);font-size:13px}
tr.run{cursor:pointer} tr.run:hover td{background:#f6f9fc}
tr.sel td{background:#e8f2fb}
tr.sel td:first-child{box-shadow:inset 3px 0 0 var(--blue)}
.best{color:var(--amber);font-weight:700}
.foul{color:var(--bad);font-weight:600}
.pill{display:inline-block;background:#eef3f8;color:var(--dim);border-radius:9px;
  padding:2px 8px;font-size:10.5px}
#pane-results #cmpbtn{background:#fff;color:var(--blue);
  border:1px solid var(--line);font-weight:600;
  box-shadow:0 3px 8px rgba(60,35,10,.28)}
#pane-results #cmpbtn.on{background:var(--blue);color:#fff;border-color:var(--blue)}

.records{display:flex;gap:30px;justify-content:center;align-items:flex-start;
  flex-wrap:wrap;margin-top:10px}
.rec{position:relative;text-align:center}
.rec .cap{color:#54381a;font-size:10.5px;font-weight:700;letter-spacing:.14em;
  text-transform:uppercase;margin-bottom:6px;
  text-shadow:0 1px 0 rgba(255,255,255,.2)}
.rec .who{color:#6b4a22;font-size:9.5px;letter-spacing:.1em;margin:4px 0 2px;
  text-transform:uppercase}
.lane{display:flex;gap:18px;justify-content:center;flex-wrap:wrap}
.rec .none{color:#6b4a22;font-size:11px;background:rgba(255,255,255,.16);
  border:1px dashed rgba(90,60,26,.45);border-radius:8px;
  padding:36px 24px;width:250px}
#overlay{position:fixed;inset:0;background:rgba(35,20,6,.62);z-index:20;
  display:none;align-items:center;justify-content:center;padding:24px}
#overlay.show{display:flex}
#overlay .inner{position:relative;filter:drop-shadow(0 16px 26px rgba(0,0,0,.5))}
#overlay .close{position:absolute;top:-14px;right:-14px;width:30px;height:30px;
  border-radius:50%;background:#fff;color:#333;border:0;font-size:17px;
  cursor:pointer;line-height:1;box-shadow:0 3px 8px rgba(0,0,0,.35);padding:0}
.up{color:var(--bad)} .down{color:var(--good)}
.empty{padding:34px;text-align:center;color:var(--dim)}

</style></head><body>
<header><div class="bar">
  <h1>FH6 Draggy</h1><span class="sub" id="count"></span>
  <select id="scope">
    <option value="class">By class</option>
    <option value="car">By car</option>
  </select>
  <select id="car"></select>
  <div class="tabs">
    <button class="tab sel" data-pane="run">Drag strip</button>
    <button class="tab" data-pane="results">Results</button>
    <button class="tab" data-pane="street">Street</button>
  </div>
</div></header>
<main>
  <div class="pane show" id="pane-run">
    <div class="frame">
    <div class="board" id="board">
      <div class="col" id="colL"></div>
      <div class="mid">
        <div class="tack"><div class="pin"></div><div id="report"></div></div>
      </div>
      <div class="col" id="colR"></div>
    </div>
    </div>
  </div>
  <div class="pane" id="pane-street">
    <div class="tack" style="margin:6px auto 30px">
      <div class="pin"></div><div id="streport"></div></div>
    <div class="sec">Roll-on bests</div>
    <div class="hist" id="rolls"></div>
    <div class="sec">Street runs</div>
    <div class="hist" id="streetlist"></div>
  </div>
  <div class="pane" id="pane-results">
    <div class="bar" style="padding:0;margin-bottom:10px">
      <button id="cmpbtn">Compare</button>
      <span class="sub" id="hint" style="color:var(--dim)"></span>
    </div>
    <div id="cmp"></div>
    <details class="fold" id="foldHist"><summary>Run history</summary>
      <div class="hist" id="list"></div>
    </details>
    <details class="fold" id="foldBoard"><summary>Leaderboard</summary>
      <p class="hintbox" id="lbnote" style="margin:8px 2px 10px"></p>
      <div class="hist" id="board2"></div>
    </details>
    <details class="fold" id="foldRec"><summary>Track records</summary>
      <div class="records slipscope" id="records"></div>
    </details>
    <p class="hintbox" id="pickhint">Choose a class or a car to open these.</p>

  </div>
</main>
<div id="overlay"><div class="inner slipscope">
  <button class="close" id="ovclose">&times;</button>
  <div id="ovslip"></div>
</div></div>
<script>
let RUNS=[],car="all",scope="class",cmpMode=false,sel=[],cur=null;
const f=(v,d=3)=>v==null?"\u2013":Number(v).toFixed(d);
const fms=v=>v==null?"\u2013":(v/1000).toFixed(3);
const inScope=r=>car==="all"||(scope==="car"?String(r.car)===car:r.cl===car);
// Strip and street are different sports for RECORDS - a downhill roll must
// not set a drag strip record. But the live card is a Draggy on the dash:
// it shows whatever you just did, wherever you did it.
const filtered=()=>RUNS.filter(r=>inScope(r)&&r.mode!=="anywhere");
const streetRuns=()=>RUNS.filter(r=>inScope(r)&&r.mode==="anywhere");
const anyScoped=()=>RUNS.filter(inScope);
const bestOf=(rows,k,lower=true)=>{const v=rows.filter(r=>!r.foul&&r[k]!=null).map(r=>r[k]);
  return v.length?(lower?Math.min(...v):Math.max(...v)):null;};

async function showReport(n, target="report"){
  if(target==="report") cur=n;
  const el=document.getElementById(target);
  el.innerHTML=await (await fetch("/report?n="+n)).text();
}



async function showRecords(){
  const d=await (await fetch(`/records?scope=${scope}&value=${car}`)).json();
  document.getElementById("records").innerHTML=d.items.map(it=>
    `<div class="rec"><div class="cap">${it.label}</div><div class="lane">`+
    it.entries.map(e=>
      `<div><div class="who">${e.who}</div>`+
      (e.html
        ? `<div class="tack" data-n="${e.n}"><div class="pin"></div>${e.html}</div>`
        : `<div class="none">no run recorded<br>at this distance</div>`)+
      `</div>`).join("")+
    `</div></div>`).join("");
  document.querySelectorAll("#records .tack").forEach(t=>
    t.onclick=()=>expand(+t.dataset.n));
}

async function expand(n){
  document.getElementById("ovslip").innerHTML=
    await (await fetch("/slip?n="+n)).text();
  document.getElementById("overlay").classList.add("show");
}

let boardCSS="";
async function showBoard(current){
  const d=await (await fetch("/slips?exclude="+current)).json();
  const L=document.getElementById("colL"), R=document.getElementById("colR");
  L.innerHTML=""; R.innerHTML="";
  d.items.forEach((it,i)=>{
    const wrap=document.createElement("div");
    wrap.className="tack mini slipscope"; wrap.dataset.n=it.n;
    wrap.innerHTML='<div class="pin"></div>'+it.html;
    wrap.onclick=()=>expand(it.n);
    (i%2?R:L).appendChild(wrap);
  });
}

function showPane(which){
  document.querySelectorAll(".pane").forEach(p=>
    p.classList.toggle("show", p.id==="pane-"+which));
  document.querySelectorAll(".tab").forEach(t=>
    t.classList.toggle("sel", t.dataset.pane===which));
}

function renderList(rows){
  if(!rows.length){document.getElementById("list").innerHTML=
    '<div class="empty">No runs yet</div>';return;}
  const bq=bestOf(rows,"quarter"),b60=bestOf(rows,"sixty");
  document.getElementById("list").innerHTML=`<table><thead><tr>
   <th>Run</th><th>When</th><th>Class</th><th>R/T</th><th>60 ft</th>
   <th>1/4</th><th>MPH</th><th>0-60</th></tr></thead><tbody>${rows.map(r=>`
   <tr class="run ${sel.includes(r.n)?'sel':''}" data-n="${r.n}">
     <td>${r.n}</td><td>${r.when.slice(5,16)}</td>
     <td><span class="pill">${r.cls}</span></td>
     <td class="${r.foul?'foul':''}">${fms(r.rt)}${r.foul?' RED':''}</td>
     <td class="${r.sixty===b60?'best':''}">${f(r.sixty)}</td>
     <td class="${r.quarter===bq?'best':''}">${f(r.quarter)}</td>
     <td>${f(r.trap,1)}</td><td>${f(r.s60)}</td></tr>`).join("")}
   </tbody></table>`;
  document.querySelectorAll("tr.run").forEach(tr=>tr.onclick=()=>pick(+tr.dataset.n));
}

function pick(n){
  if(cmpMode){
    sel=sel.includes(n)?sel.filter(x=>x!==n):[...sel,n].slice(-2);
    renderList(filtered()); if(sel.length===2) showCompare();
  } else {
    sel=[n]; renderList(filtered()); showReport(n); showBoard(n); expand(n);
  }
}

function showCompare(){
  const [a,b]=sel.map(n=>RUNS.find(r=>r.n===n));
  const rows=[["R/T","rt",3,1000],["60 ft","sixty",3,1],["1/8","eighth",3,1],
              ["1/4","quarter",3,1],["MPH","trap",1,1],["0-60","s60",3,1],
              ["100-150","roll",2,1]];
  document.getElementById("cmp").innerHTML=`<div class="sheet cmp">
   <h2>Run ${a.n} vs Run ${b.n}</h2><div class="when">side by side</div>
   <table><thead><tr><th></th><th>Run ${a.n}</th><th>Run ${b.n}</th><th>Delta</th>
   </tr></thead><tbody>${rows.map(([k,key,dp,div])=>{
     const va=a[key],vb=b[key]; if(va==null||vb==null) return "";
     const d=(vb-va)/div, better=key==="trap"?d>0:d<0;
     return `<tr><td>${k}</td><td>${(va/div).toFixed(dp)}</td>
       <td>${(vb/div).toFixed(dp)}</td>
       <td class="${better?'down':'up'}">${d>0?'+':''}${d.toFixed(dp)}</td></tr>`;
   }).join("")}</tbody></table></div>`;
}

async function renderBoard(){
  const d=await (await fetch("/leaderboard")).json();
  const el=document.getElementById("board2");
  if(!d.drivers.length){
    el.innerHTML='<div class="empty">No runs yet</div>'; return;
  }
  document.getElementById("lbnote").textContent = d.by_car
    ? "One driver in this history, so this ranks your cars. Merge a second "+
      "driver with fh6_merge.py and it becomes a driver leaderboard."
    : "";
  el.innerHTML=`<table><thead><tr><th></th>${
    d.drivers.map(x=>`<th>${x}</th>`).join("")}</tr></thead><tbody>${
    d.rows.map(r=>{
      const vals=d.drivers.map(x=>r.by[x]);
      const best=Math.min(...vals.filter(v=>v!=null));
      return `<tr><td>${r.label}</td>${vals.map(v=>
        `<td class="${v===best?'best':''}">${v!=null?v.toFixed(3):"\u2013"}</td>`
      ).join("")}</tr>`;}).join("")}</tbody></table>`;
}

async function renderStreet(){
  const rows=streetRuns();
  const el=document.getElementById("streetlist");
  el.innerHTML = rows.length ? `<table><thead><tr>
    <th>Run</th><th>When</th><th>Class</th><th>Tyres</th><th>60 ft</th>
    <th>0-60</th><th>1/4</th><th>MPH</th></tr></thead><tbody>${rows.map(r=>`
    <tr class="run" data-n="${r.n}"><td>${r.n}</td><td>${r.when.slice(5,16)}</td>
      <td><span class="pill">${r.cls}</span></td>
      <td>${r.tyres||"\u2013"}</td>
      <td>${f(r.sixty)}</td><td>${f(r.s60)}</td>
      <td>${f(r.quarter)}</td><td>${f(r.trap,1)}</td></tr>`).join("")}
    </tbody></table>`
    : '<div class="empty">No street runs yet \u2014 '+
      'run the tool with --anywhere and launch anywhere on the map</div>';
  el.querySelectorAll("tr.run").forEach(tr=>
    tr.onclick=()=>expand(+tr.dataset.n));

  const d=await (await fetch("/rollons")).json();
  document.getElementById("rolls").innerHTML = d.items.length
    ? `<table><thead><tr><th>Interval</th><th>Best</th><th>Gear</th>
       <th>When</th></tr></thead><tbody>${d.items.map(e=>`
       <tr><td>${e.pair} mph</td>
       <td class="${e.seconds!=null?'best':''}">${
         e.seconds!=null?e.seconds.toFixed(3)+"s":"\u2013"}</td>
       <td>${e.gear??"\u2013"}</td>
       <td>${e.seconds!=null
              ?(e.timestamp||"").slice(5,16).replace("T"," ")
              :"not run yet"}</td></tr>`).join("")}
       </tbody></table>`
    : '<div class="empty">No roll-ons recorded yet \u2014 pin the throttle '+
      'from 60 mph and hold it to 130</div>';
}

function setFolds(){
  const chosen = car !== "all";
  document.getElementById("foldHist").open = chosen;
  document.getElementById("foldRec").open  = chosen;
  document.getElementById("foldBoard").open = chosen;
  document.getElementById("pickhint").style.display = chosen ? "none" : "block";
}

function draw(){
  const rows=filtered();
  document.getElementById("count").textContent=
    `${rows.length} run${rows.length===1?"":"s"}`;
  renderList(rows);
  // Two Draggys, one per discipline: the strip board follows the latest
  // staged run, the street board the latest free-roam pull.
  const strip=filtered(), street=streetRuns();
  if(strip.length){ const n=sel[0]&&strip.some(r=>r.n===sel[0])
      ? sel[0] : strip[strip.length-1].n;
    showReport(n); showBoard(n); }
  else { document.getElementById("report").innerHTML=
      '<div class="empty" style="color:#54381a">No strip runs yet</div>'; }
  if(street.length){ showReport(street[street.length-1].n, "streport"); }
  else { document.getElementById("streport").innerHTML=""; }
  renderList(rows);
  showRecords(); setFolds(); renderStreet(); renderBoard();
}

async function boot(){
  // load the slip stylesheet once, for every slip anywhere on the page
  const st=document.createElement("style");
  st.textContent=await (await fetch("/slipcss")).text();
  document.head.appendChild(st);
  document.getElementById("ovclose").onclick=()=>
    document.getElementById("overlay").classList.remove("show");
  document.getElementById("overlay").onclick=e=>{
    if(e.target.id==="overlay")
      document.getElementById("overlay").classList.remove("show");};

  RUNS=await (await fetch("/api/runs")).json();
  const meta=await (await fetch("/classes")).json();
  function fillPicker(){
    const el=document.getElementById("car");
    el.innerHTML = scope==="car"
      ? `<option value="all">All cars</option>`+
        meta.cars.map(c=>`<option value="${c.o}">${c.name}</option>`).join("")
      : `<option value="all">All classes</option>`+
        meta.classes.map(c=>`<option value="${c}">Class ${c}</option>`).join("");
    car="all";
  }
  fillPicker();
  document.getElementById("scope").onchange=e=>{
    scope=e.target.value; fillPicker(); sel=[];
    document.getElementById("cmp").innerHTML=""; draw();};
  document.getElementById("car").onchange=e=>{car=e.target.value;sel=[];
    document.getElementById("cmp").innerHTML="";draw();};
  document.querySelectorAll(".tab").forEach(t=>
    t.onclick=()=>showPane(t.dataset.pane));
  document.getElementById("cmpbtn").onclick=e=>{
    cmpMode=!cmpMode; sel=[];
    e.target.className=cmpMode?"on":"";
    document.getElementById("hint").textContent=cmpMode?"pick two runs":"";
    document.getElementById("cmp").innerHTML="";
    draw();
  };
  draw();
}
boot();

// The fun of a Draggy is watching the number land right after the pull.
// Poll lightly; when a new run appears, jump the card to it.
setInterval(async ()=>{
  try{
    const fresh=await (await fetch("/api/runs")).json();
    const grew = fresh.length !== RUNS.length ||
      (fresh.length && RUNS.length &&
       fresh[fresh.length-1].when !== RUNS[RUNS.length-1].when);
    if(grew){
      RUNS=fresh;
      const live=anyScoped();
      if(live.length) sel=[live[live.length-1].n];
      draw();
    }
  }catch(e){ /* server briefly away; try again next tick */ }
}, 3000);
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            return self._send(PAGE)
        if parsed.path == "/api/runs":
            return self._send(json.dumps(summary(load())),
                              "application/json; charset=utf-8")
        if parsed.path == "/slipcss":
            # One stylesheet for every slip on the page, scoped to a class
            # rather than an id. Scoping per-container meant a slip that
            # landed in a different element silently lost its styling - which
            # is why one of them rendered as bare text on the cork.
            return self._send(scope_css(S.CSS, ".slipscope"),
                              "text/css; charset=utf-8")

        if parsed.path == "/records":
            # Fastest run at each distance. Picking a car shows two slips per
            # distance - the class record and that car's own best - so you can
            # see how far off the class pace the car is.
            args = urllib.parse.parse_qs(parsed.query)
            scope = args.get("scope", ["class"])[0]
            value = args.get("value", ["all"])[0]
            runs = load()
            event = os.environ.get("FH6_EVENT", "HORIZON FESTIVAL DRAG STRIP")

            lanes = []
            if scope == "car" and value != "all":
                owner = next((r for r in runs
                              if str(r.get("car_ordinal")) == str(value)), None)
                cls = class_of(owner) if owner else None
                if cls:
                    lanes.append((f"CLASS {cls}",
                                  [r for r in runs if class_of(r) == cls]))
                lanes.append((car_name(value).upper(),
                              [r for r in runs
                               if str(r.get("car_ordinal")) == str(value)]))
            else:
                name = "ALL CLASSES" if value == "all" else f"CLASS {value}"
                lanes.append((name, [r for r in runs if match(r, "class", value)]))

            def best_at(pool, key):
                pick, t = None, None
                for r in pool:
                    # a street launch is not a strip record
                    if r.get("foul") or r.get("mode") == "anywhere":
                        continue
                    v = S.g(r, "et", key, "s")
                    if isinstance(v, (int, float)) and (t is None or v < t):
                        pick, t = r, v
                return pick

            out = []
            for key, label in (("1/4mi", "QUARTER MILE"),
                               ("1/2mi", "HALF MILE"),
                               ("1mi", "FULL MILE")):
                entries = []
                for who, pool in lanes:
                    r = best_at(pool, key)
                    if r is None:
                        entries.append({"who": who, "html": None})
                    else:
                        n = runs.index(r) + 1
                        entries.append({"who": who, "n": n,
                                        "html": S.slip_html(
                                            r, S.best_run(runs, r), event,
                                            n, len(runs), size="med")})
                out.append({"label": label, "entries": entries})
            return self._send(json.dumps({"items": out}),
                              "application/json; charset=utf-8")

        if parsed.path == "/leaderboard":
            # Best of each metric per driver. Only meaningful once histories
            # have been merged with fh6_merge.py.
            runs = [r for r in load() if not r.get("foul")]
            who = sorted({r.get("driver") or "unknown" for r in runs})
            # With one driver a driver leaderboard is an empty page, but the
            # same table works between cars - so solo it ranks the garage,
            # and grows driver columns the moment a second person is merged.
            group_by_car = len(who) < 2
            if group_by_car:
                names = load_names()
                who = [car_name(c, names) for c in
                       sorted({r.get("car_ordinal") for r in runs
                               if r.get("car_ordinal")})]
                def owner(r):
                    return car_name(r.get("car_ordinal"), names)
            else:
                def owner(r):
                    return r.get("driver") or "unknown"
            metrics = [("1/4mi", "et", "1/4 mile"), ("60ft", "et", "60 ft"),
                       ("1/8mi", "et", "1/8 mile"), ("0-60", "accel", "0-60"),
                       ("100-150", "roll", "100-150")]
            rows = []
            for key, group, label in metrics:
                entry = {"label": label, "by": {}}
                for d in who:
                    vals = []
                    for r in runs:
                        if owner(r) != d:
                            continue
                        blob = r.get(group) or {}
                        v = blob.get(key)
                        v = v.get("s") if isinstance(v, dict) else v
                        if isinstance(v, (int, float)):
                            vals.append(v)
                    if vals:
                        entry["by"][d] = round(min(vals), 3)
                rows.append(entry)
            return self._send(json.dumps({"drivers": who, "rows": rows,
                                          "by_car": group_by_car}),
                              "application/json; charset=utf-8")

        if parsed.path == "/rollons":
            # Personal bests for each roll-on interval, by car.
            try:
                rolls = json.load(open(os.path.expanduser("~/fh6_rollons.json")))
            except (OSError, json.JSONDecodeError):
                rolls = []
            best = {}
            for e in rolls:
                k = e.get("pair")
                if not k:
                    continue
                if k not in best or e["seconds"] < best[k]["seconds"]:
                    best[k] = e
            order = ["60-130", "100-150", "100-200", "60-200", "150-250",
                     "60-100"]
            out = [dict(best[k], pair=k) if k in best else {"pair": k}
                   for k in order]
            out += [dict(v, pair=k) for k, v in best.items() if k not in order]
            return self._send(json.dumps({"items": out, "count": len(rolls)}),
                              "application/json; charset=utf-8")

        if parsed.path == "/classes":
            runs = load()
            cls = sorted({class_of(r) for r in runs} - {"?"})
            names = load_names()
            cars = [{"o": o, "name": car_name(o, names)} for o in
                    sorted({r.get("car_ordinal") for r in runs
                            if r.get("car_ordinal")})]
            return self._send(json.dumps({"classes": cls, "cars": cars}),
                              "application/json; charset=utf-8")

        if parsed.path == "/slips":
            args = urllib.parse.parse_qs(parsed.query)
            try:
                skip = int(args.get("exclude", ["0"])[0])
            except ValueError:
                skip = 0
            limit = 4
            runs = load()
            # the strip board pins strip history; street has its own card
            keep = [r for r in runs if r.get("mode") != "anywhere"]
            index_of = {id(r): i for i, r in enumerate(runs)}
            event = os.environ.get("FH6_EVENT", "HORIZON FESTIVAL DRAG STRIP")
            items = []
            for r in reversed(keep):
                n = index_of[id(r)] + 1
                if n == skip or len(items) >= limit:
                    continue
                items.append({"n": n,
                              "html": S.slip_html(r, S.best_run(runs, r),
                                                  event, n, len(runs),
                                                  compact=True)})
            return self._send(json.dumps({"items": items}),
                              "application/json; charset=utf-8")

        if parsed.path in ("/report", "/slip"):
            args = urllib.parse.parse_qs(parsed.query)
            try:
                n = int(args.get("n", ["0"])[0])
            except ValueError:
                n = 0
            runs = load()
            if not 1 <= n <= len(runs):
                return self._send("<p>no such run</p>")
            run = runs[n - 1]
            pb = S.best_run(runs, run)
            event = os.environ.get("FH6_EVENT", "HORIZON FESTIVAL DRAG STRIP")
            # The slip stylesheet styles `body`, which would repaint this
            # whole page when injected as a fragment. Strip that rule and
            # scope the rest to the detail container.
            # The report is styled by the page itself. Only the slip brings
            # its own sheet, and its variables are scoped to the container it
            # lands in - a bare :root would repaint the whole page.
            css = ""            # the page loads the slip stylesheet once
            body = (S.slip_html(run, pb, event, n, len(runs))
                    if parsed.path == "/slip"
                    else S.report_html(run, event, embed=True))
            return self._send(f"<style>{css}</style>" + body)
        self.send_error(404)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--runs", help="view someone else's history file instead "
                                   "of your own")
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 to let other machines on your network view "
                         "this. Localhost only by default")
    a = ap.parse_args()

    global HISTORY
    if a.runs:
        HISTORY = os.path.expanduser(a.runs)
        if not os.path.exists(HISTORY):
            sys.exit(f"no such file: {HISTORY}")
    runs = load()
    print(f"\n  {len(runs)} runs in {HISTORY}")
    if not runs:
        print("  (empty - drive some runs and refresh the page)")
    print(f"\n  open  http://localhost:{a.port}\n")
    if a.host == "127.0.0.1":
        print("  localhost only; nothing is exposed to the network.")
    else:
        import socket as _s
        try:
            k = _s.socket(_s.AF_INET, _s.SOCK_DGRAM); k.connect(("8.8.8.8", 80))
            ip = k.getsockname()[0]; k.close()
            print(f"  shared on your network: http://{ip}:{a.port}")
        except OSError:
            print(f"  shared on your network, port {a.port}")
        print("  anyone on the same network can view it - not the open internet.")
    print("  Ctrl-C to stop.\n")
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer((a.host, a.port), Handler) as srv:
            srv.serve_forever()
    except KeyboardInterrupt:
        print("stopped.")
    except OSError as e:
        sys.exit(f"could not bind port {a.port}: {e}")


if __name__ == "__main__":
    main()
