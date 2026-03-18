"""
PRINTOSKY LIVE DASHBOARD
=========================
Real-time via WebSocket — data pushes to browser every 3 seconds.
No page reload. New jobs appear instantly.

Run: python dashboard.py
"""

import asyncio, json, os, platform, sqlite3, threading, time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

if platform.system() == "Windows":
    DB_PATH = r"C:\Printosky\Data\jobs.db"
else:
    DB_PATH = str(Path.home() / "Printosky" / "Data" / "jobs.db")

HTTP_PORT = 5000
WS_PORT   = 5001
_ws_clients = set()

# ── data ──────────────────────────────────────────────────────────────────────
def get_db():
    if not os.path.exists(DB_PATH): return None
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row; return c

def query_stats():
    db = get_db()
    if not db: return {}
    today = datetime.now().strftime("%Y-%m-%d")
    c = db.cursor()
    c.execute("""SELECT
        COUNT(*) total,
        COUNT(CASE WHEN status='Completed' THEN 1 END) completed,
        COUNT(CASE WHEN status IN ('Received','In Progress','Printed') THEN 1 END) pending,
        COALESCE(SUM(CASE WHEN amount_collected IS NOT NULL THEN amount_collected END),0) revenue,
        COALESCE(SUM(CASE WHEN payment_mode='Cash'  THEN amount_collected END),0) cash,
        COALESCE(SUM(CASE WHEN payment_mode='UPI'   THEN amount_collected END),0) upi,
        COUNT(CASE WHEN payment_mode='Cash' AND amount_collected IS NOT NULL THEN 1 END) cash_count,
        COUNT(CASE WHEN payment_mode='UPI'  AND amount_collected IS NOT NULL THEN 1 END) upi_count
        FROM jobs WHERE DATE(received_at)=?""", (today,))
    row = dict(c.fetchone() or {})
    c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('Received','In Progress','Printed') AND DATE(received_at)<?", (today,))
    row["overdue"] = c.fetchone()[0]; db.close(); return row

def query_jobs(limit=80):
    db = get_db()
    if not db: return []
    c = db.cursor()
    c.execute("""SELECT job_id,received_at,filename,file_extension,file_size_kb,
        source,sender,status,customer_name,service_type,
        amount_quoted,amount_collected,payment_mode,completed_at
        FROM jobs ORDER BY received_at DESC LIMIT ?""", (limit,))
    rows = [dict(r) for r in c.fetchall()]; db.close(); return rows

def query_weekly():
    db = get_db()
    if not db: return []
    c = db.cursor()
    c.execute("""SELECT DATE(received_at) day,COALESCE(SUM(amount_collected),0) revenue,COUNT(*) jobs
        FROM jobs WHERE received_at>=date('now','-7 days')
        GROUP BY DATE(received_at) ORDER BY day ASC""")
    rows=[dict(r) for r in c.fetchall()]; db.close(); return rows

def query_hourly():
    db = get_db()
    if not db: return []
    today = datetime.now().strftime("%Y-%m-%d")
    c = db.cursor()
    c.execute("SELECT strftime('%H',received_at) hour,COUNT(*) jobs FROM jobs WHERE DATE(received_at)=? GROUP BY hour ORDER BY hour",(today,))
    rows=[dict(r) for r in c.fetchall()]; db.close(); return rows

def query_printer_counters():
    """Get latest counter reading for each printer."""
    db = get_db()
    if not db: return {}
    result = {}
    try:
        c = db.cursor()
        for printer in ("konica", "epson"):
            c.execute("""
                SELECT polled_at, total_pages, print_bw, copy_bw,
                       print_colour, copy_colour, method
                FROM printer_counters
                WHERE printer=?
                ORDER BY polled_at DESC LIMIT 1
            """, (printer,))
            row = c.fetchone()
            if row:
                result[printer] = dict(row)
        db.close()
    except Exception:
        pass  # table may not exist yet
    return result


def query_printer_today():
    """Get today's printer page delta (current - first reading of today)."""
    db = get_db()
    if not db: return {}
    today = datetime.now().strftime("%Y-%m-%d")
    result = {}
    try:
        c = db.cursor()
        for printer in ("konica", "epson"):
            # First reading today
            c.execute("""
                SELECT total_pages, print_bw, copy_bw, polled_at
                FROM printer_counters
                WHERE printer=? AND DATE(polled_at)=?
                ORDER BY polled_at ASC LIMIT 1
            """, (printer, today))
            first = c.fetchone()
            # Latest reading
            c.execute("""
                SELECT total_pages, print_bw, copy_bw, polled_at
                FROM printer_counters
                WHERE printer=?
                ORDER BY polled_at DESC LIMIT 1
            """, (printer,))
            latest = c.fetchone()
            if first and latest:
                result[printer] = {
                    "pages_today": (latest["total_pages"] or 0) - (first["total_pages"] or 0),
                    "print_bw_today": (latest["print_bw"] or 0) - (first["print_bw"] or 0) if latest["print_bw"] and first["print_bw"] else None,
                    "copy_bw_today":  (latest["copy_bw"]  or 0) - (first["copy_bw"]  or 0) if latest["copy_bw"]  and first["copy_bw"]  else None,
                    "since": first["polled_at"],
                }
        db.close()
    except Exception:
        pass
    return result

def build_payload():
    return {"ts":datetime.now().strftime("%d %b %Y %I:%M:%S %p"),
            "stats":query_stats(),"jobs":query_jobs(80),
            "weekly":query_weekly(),"hourly":query_hourly(),"printers":query_printer_counters(),"printer_today":query_printer_today()}

# ── websocket ─────────────────────────────────────────────────────────────────
async def ws_handler(websocket):
    _ws_clients.add(websocket)
    try:
        await websocket.send(json.dumps(build_payload()))
        await websocket.wait_closed()
    except: pass
    finally: _ws_clients.discard(websocket)

async def ws_broadcaster():
    while True:
        await asyncio.sleep(3)
        if _ws_clients:
            try:
                payload = json.dumps(build_payload())
                await asyncio.gather(*[ws.send(payload) for ws in _ws_clients.copy()], return_exceptions=True)
            except: pass

async def run_ws():
    async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT):
        await ws_broadcaster()

def start_ws_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_ws())

# ── html ──────────────────────────────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Printosky Live Tracker</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--blue:#1B3F8B;--orange:#E8500A;--green:#27AE60;--yellow:#F5A623;--bg:#F0EDE8;--card:#fff;--text:#1A1A1A;--muted:#888;--border:#E8E4DE}
body{font-family:Arial,sans-serif;background:var(--bg);color:var(--text);font-size:14px}
.hdr{background:var(--blue);color:#fff;padding:12px 22px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.25)}
.hdr-l{display:flex;align-items:center;gap:12px}
.hdr h1{font-size:18px;letter-spacing:1px}
.hdr .store{font-size:11px;opacity:.7}
.dot{width:9px;height:9px;border-radius:50%;background:#4CAF50;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.4)}}
.hdr-r{font-size:12px;opacity:.8;text-align:right}
.wrap{padding:16px 20px;max-width:1500px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:11px;margin-bottom:16px}
.card{background:var(--card);border-radius:10px;padding:14px 16px;box-shadow:0 2px 6px rgba(0,0,0,.07)}
.lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px}
.val{font-size:30px;font-weight:700;color:var(--blue);line-height:1}
.sub{font-size:11px;color:var(--muted);margin-top:4px}
.warn .val{color:var(--orange)} .ok .val{color:var(--green)}
.alert-banner{background:#fff3ed;border:1px solid var(--orange);border-radius:8px;padding:10px 16px;margin-bottom:14px;color:#7a2500;font-size:13px;display:none}
.alert-banner.show{display:block}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-bottom:13px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.sec{background:var(--card);border-radius:10px;padding:16px 18px;box-shadow:0 2px 6px rgba(0,0,0,.07);margin-bottom:13px}
.sec h2{font-size:13px;font-weight:700;color:var(--blue);margin-bottom:12px;padding-bottom:7px;border-bottom:2px solid #E8EEF8}
.pjob{display:flex;justify-content:space-between;align-items:center;padding:9px 11px;border-left:4px solid var(--orange);background:#fffaf7;margin-bottom:6px;border-radius:0 6px 6px 0}
.pjob.fresh{border-left-color:var(--yellow)}
.jid{font-weight:700;color:var(--blue);font-size:12px}
.fname{color:#444;font-size:12px;margin-left:9px}
.age-txt{font-size:12px;font-weight:600;color:var(--orange);white-space:nowrap}
.pjob.fresh .age-txt{color:var(--yellow)}
.empty-ok{color:var(--green);padding:10px;font-size:13px}
.chart{display:flex;align-items:flex-end;gap:7px;height:90px;padding:5px 0}
.bw{display:flex;flex-direction:column;align-items:center;flex:1}
.bv{font-size:10px;color:#555;margin-bottom:3px}
.bar{width:100%;max-width:36px;border-radius:3px 3px 0 0;background:var(--blue);transition:height .5s;min-height:3px}
.bl{font-size:10px;color:var(--muted);margin-top:3px}
.bj{font-size:9px;color:#aaa}
.hmap{display:flex;gap:3px;flex-wrap:wrap;align-items:flex-end}
.hcell{width:30px;height:30px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff;font-weight:600}
.hclbl{font-size:9px;color:var(--muted);text-align:center;width:30px;margin-top:2px}
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#E8EEF8;color:var(--blue);padding:8px 10px;text-align:left;font-size:11px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:hover td{background:#fafafa}
.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600;color:#fff;white-space:nowrap}
.sbadge{display:inline-block;padding:1px 7px;border-radius:4px;font-size:10px;background:#eee;color:#555}
.new-row{animation:hi .8s ease}
@keyframes hi{0%{background:#fff9e6}100%{background:transparent}}
.conn-bar{text-align:center;padding:6px;font-size:12px;display:none}
.conn-bar.offline{display:block;background:#ffebeb;color:#8B0000}
.conn-bar.online{display:block;background:#ebffef;color:#1a6b2a}
</style>
</head>
<body>
<div class="hdr">
  <div class="hdr-l">
    <div class="dot" id="dot"></div>
    <div><h1>PRINTOSKY</h1><div class="store">Oxygen Students Paradise &nbsp;·&nbsp; Live Tracker</div></div>
  </div>
  <div class="hdr-r"><div id="ts">Connecting…</div><div style="opacity:.6;margin-top:2px">Live · auto-updates</div></div>
</div>
<div class="conn-bar" id="cb"></div>
<div class="wrap">
  <div class="alert-banner" id="ob"></div>
  <div class="cards">
    <div class="card"><div class="lbl">Jobs Today</div><div class="val" id="cT">—</div><div class="sub" id="cC">—</div></div>
    <div class="card warn"><div class="lbl">Pending Now</div><div class="val" id="cP">—</div><div class="sub">need action</div></div>
    <div class="card"><div class="lbl">Revenue Today</div><div class="val" id="cR">—</div><div class="sub" id="cRS">—</div></div>
    <div class="card"><div class="lbl">Cash Jobs</div><div class="val" id="cCN">—</div><div class="sub" id="cCA">—</div></div>
    <div class="card"><div class="lbl">UPI Jobs</div><div class="val" id="cUN">—</div><div class="sub" id="cUA">—</div></div>
    <div class="card" id="ocrd"><div class="lbl">Overdue</div><div class="val" id="cO">—</div><div class="sub">from prev days</div></div>
  </div>
  <div class="grid2">
    <div class="sec"><h2>⏳ Pending — Needs Action</h2><div id="pl"><div class="empty-ok">Loading…</div></div></div>
    <div class="sec"><h2>⏰ Today by Hour</h2>
      <div style="display:flex;flex-direction:column;gap:2px">
        <div class="hmap" id="hmc"></div>
        <div class="hmap" id="hml" style="align-items:flex-start;margin-top:1px"></div>
      </div>
    </div>
  </div>
  <div class="sec" id="printer-sec"><h2>🖨️ Printer Counters</h2>
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      <div style="flex:1;min-width:220px;background:#f8f9fa;border-radius:10px;padding:16px">
        <div style="font-weight:700;margin-bottom:10px">⬛ Konica Bizhub Pro 1100</div>
        <div style="font-size:12px;color:#888;margin-bottom:8px" id="konica-ts">Not yet polled</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <div><div style="font-size:11px;color:#888">Total Pages</div><div style="font-size:22px;font-weight:700" id="konica-total">—</div></div>
          <div><div style="font-size:11px;color:#888">Print B&W</div><div style="font-size:18px;font-weight:600" id="konica-pbw">—</div></div>
          <div><div style="font-size:11px;color:#888">Copy B&W</div><div style="font-size:18px;font-weight:600" id="konica-cbw">—</div></div>
          <div><div style="font-size:11px;color:#888">Method</div><div style="font-size:13px;color:#999" id="konica-method">—</div></div>
        </div>
      </div>
      <div style="flex:1;min-width:220px;background:#f0f7ff;border-radius:10px;padding:16px">
        <div style="font-weight:700;margin-bottom:10px">🎨 Epson WF-C21000</div>
        <div style="font-size:12px;color:#888;margin-bottom:8px" id="epson-ts">Not yet polled</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <div><div style="font-size:11px;color:#888">Total Pages</div><div style="font-size:22px;font-weight:700" id="epson-total">—</div></div>
          <div><div style="font-size:11px;color:#888">Method</div><div style="font-size:13px;color:#999" id="epson-method">—</div></div>
        </div>
      </div>
    </div>
  </div>

  <div class="sec"><h2>📊 Revenue — Last 7 Days</h2><div class="chart" id="wc"><div style="color:#aaa;font-size:12px">No data yet</div></div></div>
  <div class="sec" id="verify-sec">
    <h2>🔍 Cross-Verify — Jobs vs Pages Printed Today</h2>
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px">
      <div style="flex:1;min-width:180px;background:#f8f9fa;border-radius:10px;padding:14px">
        <div style="font-size:11px;color:#888;margin-bottom:4px">Files Received Today</div>
        <div style="font-size:28px;font-weight:700" id="cv-files">—</div>
        <div style="font-size:11px;color:#888;margin-top:4px">jobs in hot folder</div>
      </div>
      <div style="flex:1;min-width:180px;background:#f0f7ff;border-radius:10px;padding:14px">
        <div style="font-size:11px;color:#888;margin-bottom:4px">Konica Pages Today</div>
        <div style="font-size:28px;font-weight:700" id="cv-konica">—</div>
        <div style="font-size:11px;color:#aaa;margin-top:4px" id="cv-konica-detail">print + copy</div>
      </div>
      <div style="flex:1;min-width:180px;background:#f0fff4;border-radius:10px;padding:14px">
        <div style="font-size:11px;color:#888;margin-bottom:4px">Epson Pages Today</div>
        <div style="font-size:28px;font-weight:700" id="cv-epson">—</div>
        <div style="font-size:11px;color:#aaa;margin-top:4px" id="cv-epson-detail">total colour</div>
      </div>
      <div style="flex:1;min-width:180px;background:#fff8e1;border-radius:10px;padding:14px">
        <div style="font-size:11px;color:#888;margin-bottom:4px">Completed Jobs</div>
        <div style="font-size:28px;font-weight:700" id="cv-done">—</div>
        <div style="font-size:11px;color:#aaa;margin-top:4px">marked done today</div>
      </div>
    </div>
    <div style="font-size:12px;color:#888;background:#fffbea;border-radius:8px;padding:10px 14px" id="cv-note">
      Printer counters update every 5 minutes. Use this to spot files received but not printed.
    </div>
  </div>
  <div class="sec"><h2>📋 All Jobs</h2>
    <div class="tbl-wrap">
      <table><thead><tr>
        <th>Job ID</th><th>Time</th><th>File</th><th>Type</th><th>Source</th><th>From</th>
        <th>Customer</th><th>Quoted</th><th>Collected</th><th>Mode</th><th>Age</th><th>Status</th>
      </tr></thead><tbody id="jt"><tr><td colspan="12" style="text-align:center;padding:30px;color:#aaa">Loading…</td></tr></tbody></table>
    </div>
  </div>
</div>
<script>
const $=id=>document.getElementById(id);
const fmt=n=>typeof n==='number'?'₹'+Math.round(n).toLocaleString('en-IN'):'—';
const esc=s=>(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
function age(ts){if(!ts)return'';const d=Math.floor((Date.now()-new Date(ts))/60000);return d<60?d+'m':Math.floor(d/60)+'h '+(d%60)+'m';}
function sbadge(s){const c={Received:'#E8500A','In Progress':'#F5A623',Printed:'#4A90D9',Completed:'#27AE60',Cancelled:'#999'};return`<span class="badge" style="background:${c[s]||'#888'}">${esc(s)}</span>`;}
let prev=new Set();
function render(data){
  const{ts,stats,jobs,weekly,hourly,printers,printer_today}=data;
  // Printer counters
  const konica=(printers||{}).konica;
  const epson=(printers||{}).epson;
  if(konica){$('konica-total').textContent=konica.total_pages!=null?konica.total_pages.toLocaleString():'—';$('konica-pbw').textContent=konica.print_bw!=null?konica.print_bw.toLocaleString():'—';$('konica-cbw').textContent=konica.copy_bw!=null?konica.copy_bw.toLocaleString():'—';$('konica-ts').textContent='Last polled: '+konica.polled_at;$('konica-method').textContent=konica.method||'—';}
  if(epson){$('epson-total').textContent=epson.total_pages!=null?epson.total_pages.toLocaleString():'—';$('epson-ts').textContent='Last polled: '+epson.polled_at;$('epson-method').textContent=epson.method||'—';}
  // Cross-verify panel
  const pt=printer_today||{};
  const todayJobs=jobs.filter(j=>j.received_at&&j.received_at.startsWith(new Date().toISOString().slice(0,10)));
  const doneToday=todayJobs.filter(j=>j.status==='Completed').length;
  $('cv-files').textContent=todayJobs.length;
  $('cv-done').textContent=doneToday;
  if(pt.konica){
    $('cv-konica').textContent=(pt.konica.pages_today||0).toLocaleString();
    const pb=pt.konica.print_bw_today!=null?'Print: '+pt.konica.print_bw_today.toLocaleString():'';
    const cb=pt.konica.copy_bw_today!=null?' Copy: '+pt.konica.copy_bw_today.toLocaleString():'';
    $('cv-konica-detail').textContent=(pb+cb)||'since '+((pt.konica.since||'').slice(11,16));
  }
  if(pt.epson){$('cv-epson').textContent=(pt.epson.pages_today||0).toLocaleString();$('cv-epson-detail').textContent='since '+((pt.epson.since||'').slice(11,16));}
  // Warn if pending jobs exist for a long time
  const pending=todayJobs.filter(j=>['Received','In Progress','Printed'].includes(j.status));
  if(pending.length>0&&(pt.konica||pt.epson)){
    $('cv-note').style.background='#fff3cd';
    $('cv-note').textContent='⚠️  '+pending.length+' job(s) still pending. Check if files were printed without being marked done.';
  } else if(doneToday===todayJobs.length&&todayJobs.length>0){
    $('cv-note').style.background='#d4edda';
    $('cv-note').textContent='✅ All jobs today are completed.';
  }
  $('ts').textContent=ts;
  $('cT').textContent=stats.total||0; $('cC').textContent=(stats.completed||0)+' completed';
  $('cP').textContent=stats.pending||0;
  $('cR').textContent=fmt(stats.revenue); $('cRS').textContent='Cash '+fmt(stats.cash)+' · UPI '+fmt(stats.upi);
  $('cCN').textContent=stats.cash_count||0; $('cCA').textContent=fmt(stats.cash);
  $('cUN').textContent=stats.upi_count||0; $('cUA').textContent=fmt(stats.upi);
  $('cO').textContent=stats.overdue||0;
  const oc=$('ocrd'),ob=$('ob');
  if(stats.overdue>0){oc.classList.add('warn');ob.textContent='⚠️  '+stats.overdue+' job(s) from previous days still pending.';ob.classList.add('show');}
  else{oc.classList.remove('warn');oc.classList.add('ok');ob.classList.remove('show');}
  const pend=jobs.filter(j=>['Received','In Progress','Printed'].includes(j.status));
  const pl=$('pl');
  if(!pend.length){pl.innerHTML='<div class="empty-ok">✅ No pending jobs</div>';}
  else{pl.innerHTML=pend.map(j=>{const mins=Math.floor((Date.now()-new Date(j.received_at))/60000);return`<div class="pjob ${mins<30?'fresh':''}"><div><span class="jid">${esc(j.job_id)}</span><span class="fname">${esc(j.filename.slice(0,40))}${j.filename.length>40?'…':''}</span></div><div class="age-txt">${age(j.received_at)} waiting</div></div>`;}).join('');}
  if(weekly.length){const mx=Math.max(...weekly.map(w=>w.revenue),1);$('wc').innerHTML=weekly.map(w=>{const h=Math.max(4,Math.round(w.revenue/mx*80));return`<div class="bw"><div class="bv">${fmt(w.revenue)}</div><div class="bar" style="height:${h}px"></div><div class="bl">${w.day.slice(8)}</div><div class="bj">${w.jobs}j</div></div>`;}).join('');}
  const hm={};(hourly||[]).forEach(h=>{hm[h.hour]=h.jobs;});
  const mh=Math.max(...Object.values(hm),1);
  let c1='',c2='';
  for(let h=8;h<=19;h++){const hr=String(h).padStart(2,'0');const n=hm[hr]||0;const p=n/mh;const r=Math.round(27+p*200),g=Math.round(63+p*80),b=Math.round(139-p*100);const bg=n===0?'#e8e8e8':`rgb(${r},${g},${b})`;c1+=`<div class="hcell" style="background:${bg}" title="${h}:00 — ${n} jobs">${n||''}</div>`;c2+=`<div class="hclbl">${h}</div>`;}
  $('hmc').innerHTML=c1; $('hml').innerHTML=c2;
  const newIds=new Set(jobs.map(j=>j.job_id));
  $('jt').innerHTML=jobs.map(j=>{const isNew=!prev.has(j.job_id)&&prev.size>0;return`<tr class="${isNew?'new-row':''}"><td style="font-weight:700;color:#1B3F8B;white-space:nowrap">${esc(j.job_id)}</td><td style="white-space:nowrap">${(j.received_at||'').slice(11,16)}</td><td title="${esc(j.filename)}">${esc(j.filename.slice(0,32))}${j.filename.length>32?'…':''}</td><td><span class="sbadge">${esc(j.file_extension||'?')}</span></td><td><span class="sbadge">${esc(j.source||'')}</span></td><td style="color:#555;font-size:11px">${esc((j.sender||'').slice(0,16))}</td><td>${esc(j.customer_name||'—')}</td><td>${j.amount_quoted?fmt(j.amount_quoted):'—'}</td><td style="font-weight:600">${j.amount_collected?fmt(j.amount_collected):'—'}</td><td>${esc(j.payment_mode||'—')}</td><td style="white-space:nowrap;color:#888">${age(j.received_at)}</td><td>${sbadge(j.status)}</td></tr>`;}).join('')||'<tr><td colspan="12" style="text-align:center;padding:30px;color:#aaa">No jobs yet</td></tr>';
  prev=newIds;
}
let ws,rt;
const dot=$('dot'),cb=$('cb');
function connect(){
  ws=new WebSocket('ws://'+location.hostname+':WS_PORT_PLACEHOLDER');
  ws.onopen=()=>{dot.style.background='#4CAF50';cb.className='conn-bar online';cb.textContent='● Connected — live data';setTimeout(()=>{cb.className='conn-bar'},3000);clearTimeout(rt);};
  ws.onmessage=e=>{try{render(JSON.parse(e.data));}catch(e){}};
  ws.onclose=()=>{dot.style.background='#E8500A';cb.className='conn-bar offline';cb.textContent='⚠ Reconnecting…';rt=setTimeout(connect,3000);};
  ws.onerror=()=>ws.close();
}
connect();
setInterval(()=>{if(ws&&ws.readyState===1)ws.send('ping');},10000);
</script>
</body>
</html>""".replace("WS_PORT_PLACEHOLDER", str(WS_PORT))

# ── http ──────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if self.path in ("/", "/dashboard"):
            body=DASHBOARD_HTML.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path=="/api/data":
            body=json.dumps(build_payload()).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        else: self.send_response(404); self.end_headers()

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}\nStart watcher.py first.\n"); return
    if not WS_AVAILABLE:
        print("Run: pip install websockets"); return
    threading.Thread(target=start_ws_thread, daemon=True).start()
    time.sleep(0.5)
    print(f"""
╔══════════════════════════════════════════════════╗
║      PRINTOSKY LIVE DASHBOARD — RUNNING          ║
║                                                  ║
║  Browser:   http://localhost:{HTTP_PORT}                ║
║  WebSocket: ws://localhost:{WS_PORT}                 ║
║  Updates every 3 seconds — no reload needed      ║
╚══════════════════════════════════════════════════╝
""")
    server = HTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nStopped.")

if __name__ == "__main__":
    main()
