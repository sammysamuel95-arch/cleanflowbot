"""
CleanFlowBot — Mission Control v5
Run: python3 panel.py
Open: http://localhost:8888

Layout: Market Table + Phase-Filtered Log + Command Console
Phase filters: Discovery | Monitor | Fire Gate | Fire | Session | Errors
"""
import json, os, re, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
CONFIG_PATH = os.path.join(DATA_DIR, 'bot_config.json')
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'log', 'bot_output.log')

CMD_IN_PATH = os.path.join(DATA_DIR, 'cmd_in.json')
CMD_OUT_PATH = os.path.join(DATA_DIR, 'cmd_out.json')
CMD_LOG_PATH = os.path.join(DATA_DIR, 'cmd_log.json')


def send_command(cmd: str) -> dict:
    import time as _time
    ts = str(_time.time())
    try:
        with open(CMD_IN_PATH, 'w') as f:
            json.dump({'cmd': cmd, 'ts': ts}, f)
    except Exception as e:
        return {'ok': False, 'msg': f'Write failed: {e}'}
    deadline = _time.time() + 5.0
    while _time.time() < deadline:
        _time.sleep(0.1)
        try:
            with open(CMD_OUT_PATH) as f:
                out = json.load(f)
            if out.get('ts') == ts:
                return out
        except Exception:
            pass
    return {'ok': False, 'msg': 'Timeout waiting for bot response (5s)'}


def load_config():
    try:
        with open(CONFIG_PATH) as f: return json.load(f)
    except Exception:
        return {}

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f: json.dump(cfg, f, indent=2)


def tail_log(n=500, filt=None):
    try:
        read_lines = max(n * 3, 1500)
        chunk = 1024 * 256  # 256KB — enough for ~1500 log lines
        with open(LOG_PATH, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            pos = max(0, size - chunk)
            f.seek(pos)
            raw = f.read()
        text = raw.decode('utf-8', errors='replace')
        # Drop partial first line if we seeked mid-file
        if pos > 0:
            text = text[text.find('\n') + 1:]
        out = [l.rstrip() for l in text.splitlines()][-read_lines:]
        if filt:
            out = [l for l in out if filt.lower() in l.lower()]
        return out[-n:]
    except Exception:
        return []


def get_bot_pid():
    """Get main.py process ID."""
    import subprocess
    try:
        result = subprocess.run(['pgrep', '-if', 'python.*main.py'],
                                capture_output=True, text=True, timeout=2)
        pids = [p.strip() for p in result.stdout.strip().split('\n') if p.strip()]
        return int(pids[0]) if pids else None
    except Exception:
        return None


def get_bot_start_time():
    """Get main.py process start time as unix timestamp."""
    import subprocess
    pid = get_bot_pid()
    if not pid:
        return None
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                                capture_output=True, text=True, timeout=2)
        lstart = result.stdout.strip()
        if lstart:
            import datetime
            dt = datetime.datetime.strptime(lstart, '%a %b %d %H:%M:%S %Y')
            return dt.timestamp()
    except Exception:
        pass
    return None


def get_status():
    dash_path = os.path.join(os.path.dirname(__file__), 'data', 'dash_state.json')
    try:
        with open(dash_path, 'r') as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'ws': False, 'markets': [], 'tracked': 0, 'listing': 0,
                'store': 0, 'warns': 0, 'errors': 0}

    age = time.time() - state.get('updated_at', state.get('ts', 0))
    state['stale'] = age > 10
    state['age'] = round(age, 1)

    markets = []
    for m in state.get('markets', []):
        markets.append({
            'n': m.get('n', ''), 'b': m.get('b', -999),
            'e1': m.get('e1'), 'e2': m.get('e2'),
            'etop': m.get('etop', '–'), 'pf': m.get('pf', '–'),
            'pa': m.get('pa', 0), 's': m.get('s', 0),
            'st': m.get('st', 'DISCOVERED'), 'ln': m.get('ln', 0),
            'ps': m.get('ps', ''), 'mid': m.get('mid', ''),
            'pool': m.get('pool', -1), 'no_line': m.get('no_line', False),
            'can_press': m.get('can_press', True), 'cp': m.get('cp', True),
            'locked_at': m.get('locked_at', 0), 'fk': m.get('fk', ''),
            'inv_value': m.get('inv_value', 0), 'inv_items': m.get('inv_items', 0),
            'game': m.get('game', ''),
            'ml': m.get('ml', ''),
            'remain_zero_at': m.get('remain_zero_at'),
        })

    warnings = 0
    errors = 0
    try:
        with open(LOG_PATH, 'r') as f:
            for line in f.readlines()[-500:]:
                if '[WARN]' in line: warnings += 1
                if '[ERROR]' in line: errors += 1
    except Exception:
        pass

    ev_pos = sum(1 for m in markets if (m.get('b') or -999) > 0)
    fire_count = sum(1 for m in markets if 0 < m.get('s', 999) <= 50)

    return {
        'ws': state.get('ws', False), 'pid': get_bot_pid(), 'bot_start': get_bot_start_time(), 'markets': markets,
        'live': state.get('live', []),
        'tracking': state.get('tracked', 0), 'ev_pos': ev_pos,
        'fires': fire_count, 'listing': state.get('listing', 0),
        'warns': warnings, 'errors': errors,
        'stale': state.get('stale', False), 'age': state.get('age', 0),
        'remain_at': state.get('remain_at', time.time()),
        'bag_value': state.get('bag_value', 0),
        'bag_count': state.get('bag_count', 0),
        'session_tracker': state.get('session_tracker', None),
        'bus_freshness': state.get('bus_freshness', {}),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HTML
# ═══════════════════════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CleanFlowBot — Mission Control</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#06080c;--s1:#0b0e14;--s2:#10141c;--s3:#161c28;
  --b1:#1a2234;--b2:#243050;
  --t1:#c8d0e0;--t2:#6b7a94;--t3:#3d4b64;
  --g:#00e676;--gd:#00e67618;--g2:#00c853;
  --r:#ff3d57;--rd:#ff3d5718;
  --y:#ffc107;--yd:#ffc10718;
  --bl:#448aff;--bld:#448aff18;
  --p:#b388ff;--pd:#b388ff18;
  --c:#00e5ff;--cd:#00e5ff18;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'JetBrains Mono',monospace;background:var(--bg);color:var(--t1);font-size:11px;overflow:hidden;height:100vh}

/* ── Header ─────────────────────────────────────────── */
.hd{height:40px;display:flex;align-items:center;justify-content:space-between;padding:0 16px;background:var(--s1);border-bottom:1px solid var(--b1)}
.hd-left{display:flex;align-items:center;gap:12px}
.hd h1{font-family:'Outfit',sans-serif;font-size:14px;font-weight:700;color:var(--t1)}
.hd h1 b{color:var(--g);font-weight:800}
.hd h1 span{color:var(--t3);font-weight:400;font-size:10px;margin-left:6px}
.ws{display:flex;align-items:center;gap:5px}
.ws-dot{width:7px;height:7px;border-radius:50%}
.ws-dot.on{background:var(--g);box-shadow:0 0 6px var(--g)}
.ws-dot.off{background:var(--r);box-shadow:0 0 6px var(--r);animation:blink 1s infinite}
@keyframes blink{50%{opacity:.3}}
.ws-label{font-size:10px;font-weight:600}
.hd-right{display:flex;align-items:center;gap:14px;font-size:10px;color:var(--t3)}
.hd-age{font-size:9px;padding:2px 6px;border-radius:3px;background:var(--s2);border:1px solid var(--b1);font-family:JetBrains Mono,monospace}
.hd-age.age-ok{color:var(--g);border-color:var(--g)}
.hd-age.age-warn{color:#7dd3fc;border-color:#7dd3fc}
.hd-age.age-stale{color:var(--r);border-color:var(--r)}
.sess-badge{font-size:9px;padding:2px 7px;border-radius:3px;font-family:JetBrains Mono,monospace;border:1px solid}
.sess-badge.up{color:var(--g);border-color:var(--g);background:rgba(74,222,128,.08)}
.sess-badge.down{color:var(--r);border-color:var(--r);background:rgba(248,113,113,.08)}

/* ── Stats Bar ──────────────────────────────────────── */
.stats{display:flex;border-bottom:1px solid var(--b1);background:var(--s1)}
.st{flex:1;padding:6px 14px;border-right:1px solid var(--b1);min-width:0}
.st:last-child{border:none}
.st-label{font-size:7px;text-transform:uppercase;letter-spacing:1.5px;color:var(--t3)}
.st-val{font-family:'Outfit',sans-serif;font-size:17px;font-weight:700;font-variant-numeric:tabular-nums}

/* ── Layout ─────────────────────────────────────────── */
.layout{display:grid;grid-template-columns:220px 1fr;height:calc(100vh - 76px)}

/* ── Sidebar ────────────────────────────────────────── */
.side{background:var(--s1);border-right:1px solid var(--b1);overflow-y:auto;display:flex;flex-direction:column;font-size:10px}
.sec{padding:8px 0}
.sec-title{font-size:7px;text-transform:uppercase;letter-spacing:2px;color:var(--g);padding:0 12px 4px;font-weight:600}
.sec+.sec{border-top:1px solid var(--b1)}
.row{display:flex;align-items:center;padding:2px 12px;gap:6px}
.row-k{flex:1;font-size:10px;color:var(--t2)}
.row-v{width:60px;background:var(--bg);border:1px solid var(--b1);color:var(--t1);font-family:'JetBrains Mono',monospace;font-size:10px;padding:2px 6px;border-radius:2px;text-align:right}
.row-v:focus{border-color:var(--g);outline:none}
.save-bar{padding:6px 12px;border-top:1px solid var(--b1);display:flex;gap:6px;margin-top:auto;align-items:center}
.btn{font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:600;padding:4px 10px;border-radius:3px;border:none;cursor:pointer;transition:.15s}
.btn-g{background:var(--g);color:var(--bg)}
.btn-g:hover{background:var(--g2)}
.btn-o{background:transparent;color:var(--t2);border:1px solid var(--b1)}
.btn-o:hover{border-color:var(--t2);color:var(--t1)}
.btn-r{background:transparent;color:var(--r);border:1px solid #ff3d5730}
.btn-r:hover{background:var(--rd);border-color:var(--r)}
.toast{font-size:9px;color:var(--g);opacity:0;transition:.3s;margin-left:auto}
.toast.on{opacity:1}

/* ── Main Content ───────────────────────────────────── */
.main{display:flex;flex-direction:column;overflow:hidden}

/* ── Market Table Section ───────────────────────────── */
.mkt-section{border-bottom:1px solid var(--b1);display:flex;flex-direction:column;height:35vh;min-height:60px;overflow:hidden}
.mkt-section.collapsed{height:32px!important;min-height:32px;overflow:hidden}
.mkt-header{display:flex;align-items:center;gap:8px;padding:4px 12px;background:var(--s1);border-bottom:1px solid var(--b1);cursor:pointer;flex-shrink:0}
.mkt-header h2{font-family:'Outfit',sans-serif;font-size:11px;font-weight:600;color:var(--g);text-transform:uppercase;letter-spacing:1px}
.mkt-header .mkt-count{font-size:9px;color:var(--t3)}
.mkt-toggle{margin-left:auto;font-size:9px;color:var(--t3);cursor:pointer;background:none;border:none;font-family:inherit}
.mkt-tab{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;padding:4px 14px;border:none;border-bottom:2px solid transparent;background:transparent;color:var(--t3);cursor:pointer;text-transform:uppercase;letter-spacing:.5px}
.mkt-tab:hover{color:var(--t2)}
.mkt-tab.active{color:var(--g);border-bottom-color:var(--g)}
.mkt-filter{display:flex;align-items:center;gap:6px;padding:4px 12px;background:var(--s1);flex-shrink:0}
.mkt-filter input[type=text]{background:var(--bg);border:1px solid var(--b1);color:var(--t1);font-family:inherit;font-size:10px;padding:3px 8px;border-radius:2px;width:160px}
.mkt-filter input[type=text]:focus{border-color:var(--g);outline:none}
.mkt-filter label{font-size:9px;color:var(--t3);cursor:pointer;display:flex;align-items:center;gap:3px}
.mkt-filter input[type=checkbox]{accent-color:var(--g);width:11px;height:11px}
.mkt-scroll{overflow-y:auto;flex:1}

/* Table */
.mt{width:100%;border-collapse:collapse}
.mt th{font-size:7px;text-transform:uppercase;letter-spacing:1px;color:var(--t3);text-align:left;padding:4px 8px;border-bottom:1px solid var(--b1);position:sticky;top:0;background:var(--s1);font-weight:500;cursor:pointer;user-select:none;white-space:nowrap}
.mt th.tc,.mt td.tc{text-align:center}
.mt th:hover{color:var(--t2)}
.mt th.sorted{color:var(--g)}
.mt td{padding:3px 8px;border-bottom:1px solid #080b10;font-variant-numeric:tabular-nums;font-size:10px}
.mt tr:hover{background:var(--s2)}
.mt .name{max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px}
.evp{color:var(--g);font-weight:600}.evh{color:#06080c;background:var(--g);padding:1px 4px;border-radius:2px;font-weight:700;font-size:9px}
.evn{color:var(--t3)}.ev-no{color:var(--r);font-size:9px}
.sec-h{color:var(--r);font-weight:600}.sec-w{color:var(--y)}.sec-c{color:#7dd3fc}
.badge{font-size:7px;padding:2px 5px;border-radius:2px;font-weight:600;letter-spacing:.5px;text-transform:uppercase}
.b-fire{color:var(--g);background:var(--gd);animation:pls 1.5s infinite}
.b-ext{color:var(--y);background:var(--yd)}
.b-watch{color:var(--bl);background:var(--bld)}
.b-nodata{color:var(--r);background:var(--rd)}
.b-disc{color:#4ade80;background:#2a4a3a}
@keyframes pls{0%,100%{opacity:1}50%{opacity:.4}}

.resize-handle{height:5px;background:var(--b1);cursor:row-resize;flex-shrink:0;transition:background .15s;position:relative}
.resize-handle:hover,.resize-handle.dragging{background:var(--g);opacity:.5}
.resize-handle::after{content:'';position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:30px;height:2px;background:var(--t3);border-radius:1px}
.resize-handle:hover::after{background:var(--g)}

/* ── Log Section ────────────────────────────────────── */
.log-section{flex:1;display:flex;flex-direction:column;overflow:hidden;min-height:0}
.log-toolbar{display:flex;align-items:center;gap:6px;padding:5px 12px;background:var(--s1);border-bottom:1px solid var(--b1);flex-wrap:wrap;flex-shrink:0}
.phase-btn{font-size:9px;padding:3px 8px;border-radius:10px;cursor:pointer;border:1px solid var(--b1);color:var(--t2);transition:.15s;user-select:none;display:inline-flex;align-items:center;gap:4px;font-family:inherit;background:transparent}
.phase-btn:hover{border-color:var(--t2)}
.phase-btn.on{font-weight:600}
.phase-btn .dot{width:6px;height:6px;border-radius:50%;opacity:.5}
.phase-btn.on .dot{opacity:1;box-shadow:0 0 4px currentColor}
.phase-btn .cnt{font-size:8px;opacity:.6}
.log-search{background:var(--bg);border:1px solid var(--b1);color:var(--t1);font-family:inherit;font-size:10px;padding:3px 8px;border-radius:2px;width:140px;margin-left:auto}
.log-search:focus{border-color:var(--g);outline:none}
.log-content{flex:1;overflow-y:auto;padding:2px 0;min-height:0}
.log-line{padding:1px 12px;font-size:10px;line-height:1.5;display:flex;gap:8px;border-left:2px solid transparent;white-space:nowrap}
.log-line:hover{background:var(--s2)}
.log-ts{color:var(--t3);min-width:60px;flex-shrink:0}
.log-msg{white-space:pre-wrap;word-break:break-all;flex:1;overflow:hidden}
.l-fire{border-left-color:var(--g)}.l-fire .log-msg{color:var(--g)}
.l-error{border-left-color:var(--r)}.l-error .log-msg{color:var(--r)}
.l-warn{border-left-color:var(--y)}.l-warn .log-msg{color:var(--y)}
.l-gate{border-left-color:var(--y)}
.l-session{border-left-color:var(--c)}
.l-discovery{border-left-color:var(--bl)}
.l-monitor{border-left-color:var(--p)}

/* ── Console Section ────────────────────────────────── */
.console{border-top:1px solid var(--b1);background:var(--s1);display:flex;flex-direction:column;flex-shrink:0;max-height:200px;min-height:32px;transition:max-height .2s}
.console.collapsed{max-height:32px;overflow:hidden}
.console-header{display:flex;align-items:center;padding:4px 12px;cursor:pointer;gap:8px;border-bottom:1px solid var(--b1);flex-shrink:0}
.console-header h3{font-family:'Outfit',sans-serif;font-size:10px;font-weight:600;color:var(--c);text-transform:uppercase;letter-spacing:1px}
.console-toggle{margin-left:auto;font-size:9px;color:var(--t3);background:none;border:none;cursor:pointer;font-family:inherit}
.quick-row{display:flex;gap:4px;padding:4px 12px;flex-wrap:wrap;flex-shrink:0}
.qbtn{font-family:inherit;font-size:9px;padding:3px 8px;border-radius:3px;border:1px solid var(--b1);background:var(--s2);color:var(--t2);cursor:pointer;transition:.15s}
.qbtn:hover{border-color:var(--c);color:var(--c)}
.qbtn.active{border-color:var(--g);color:var(--g)}
.qbtn.resub{color:var(--p);border-color:#b388ff30}
.qbtn.resub:hover{border-color:var(--p);background:var(--pd)}
.cmd-row{display:flex;gap:6px;padding:4px 12px;flex-shrink:0}
.cmd-input{flex:1;background:var(--bg);border:1px solid var(--b1);color:var(--g);font-family:'JetBrains Mono',monospace;font-size:11px;padding:4px 8px;border-radius:2px}
.cmd-input:focus{border-color:var(--g);outline:none}
.cmd-input::placeholder{color:var(--t3)}
.cmd-run{font-family:inherit;font-size:9px;font-weight:600;padding:4px 12px;border-radius:3px;border:1px solid var(--g);background:transparent;color:var(--g);cursor:pointer;transition:.15s}
.cmd-run:hover{background:var(--gd)}
.cmd-output{flex:1;overflow-y:auto;padding:4px 12px;min-height:0}
.cmd-entry{color:var(--t2);padding:1px 0}
.cmd-prompt{color:var(--g);font-weight:700}
.cmd-text{color:var(--c)}
.cmd-result{padding:2px 0 4px 16px;border-bottom:1px solid var(--b1);margin-bottom:4px}
.cmd-result pre{font-family:inherit;font-size:10px;white-space:pre-wrap;word-break:break-all;margin:0;line-height:1.5}
.cmd-ok pre{color:var(--t1)}
.cmd-fail pre{color:var(--r)}
.cmd-loading{color:var(--t3);font-style:italic;padding:2px 0 2px 16px}

/* ── Utility ────────────────────────────────────────── */
.g{color:var(--g)}.r{color:var(--r)}.y{color:var(--y)}.bl{color:var(--bl)}.p{color:var(--p)}.c{color:var(--c)}
@media(max-width:900px){.layout{grid-template-columns:1fr}.side{display:none}}
</style>
</head>
<body>

<!-- ═══ HEADER ═══ -->
<div class="hd">
  <div class="hd-left">
    <h1><b>CleanFlow</b>Bot <span class="ws-dot" id="wsDot" style="margin-left:8px;margin-right:2px"></span><span class="ws-label" id="wsLabel" style="margin-right:12px">...</span><span id="botPid" style="color:#7dd3fc;font-family:JetBrains Mono,monospace;font-size:9px"></span><span id="botUptime" style="color:#7dd3fc;font-family:JetBrains Mono,monospace;font-size:9px;margin-left:6px"></span><span id="sessUp" class="sess-badge up" style="margin-left:10px;display:none"></span><span id="sessDown" class="sess-badge down" style="margin-left:4px;display:none"></span></h1>
  </div>
  <div class="hd-right">
    <span id="xBagHd" style="color:#7dd3fc;font-family:JetBrains Mono,monospace;font-size:9px;margin-right:8px">–</span>
    <span id="sseStatus" class="hd-age" style="margin-right:8px;color:var(--t3)">SSE ○</span>
    <span id="busFresh" class="hd-age" style="margin-right:8px"></span>
    <span id="clk" style="color:#7dd3fc;font-family:JetBrains Mono,monospace;font-size:9px"></span>
  </div>
</div>

<!-- ═══ STATS BAR ═══ -->
<div class="stats">
  <div class="st"><div class="st-label">Tracking</div><div class="st-val bl" id="xTrack">–</div></div>
  <div class="st"><div class="st-label">EV+</div><div class="st-val g" id="xEvp">–</div></div>
  <div class="st"><div class="st-label">Fire Zone</div><div class="st-val g" id="xFires">–</div></div>
  <div class="st"><div class="st-label">Warnings</div><div class="st-val y" id="xWarns">0</div></div>
  <div class="st"><div class="st-label">Errors</div><div class="st-val" id="xErr" style="color:var(--t2)">0</div></div>
  <div class="st"><div class="st-label">Listing</div><div class="st-val" id="xList" style="color:var(--t2)">–</div></div>
</div>

<!-- ═══ LAYOUT ═══ -->
<div class="layout">

  <!-- ─── Sidebar ─── -->
  <div class="side">
    <div class="sec">
      <div class="sec-title">Firing</div>
      <div class="row"><span class="row-k">P1_EV %</span><input class="row-v" type="number" step="0.1" id="PHASE1_EV"></div>
      <div class="row"><span class="row-k">P2_EV %</span><input class="row-v" type="number" step="0.1" id="PHASE2_EV"></div>
      <div class="row"><span class="row-k">P3_EV %</span><input class="row-v" type="number" step="0.1" id="PHASE3_EV"></div>
      <div class="row"><span class="row-k">TRIGGER_S</span><input class="row-v" type="number" id="TRIGGER_SECS"></div>
      <div class="row"><span class="row-k">MIN_POOL</span><input class="row-v" type="number" id="MIN_RAW_POOL"></div>
      <div class="row"><span class="row-k">BUFFER_S</span><input class="row-v" type="number" id="EXTENSION_SECS"></div>
      <div class="row"><span class="row-k">MAX_ITEMS</span><input class="row-v" type="number" id="MAX_ITEMS"></div>
      <div class="row"><span class="row-k">ITEM_VAL $</span><input class="row-v" type="number" id="ITEM_VALUE"></div>
      <div class="row"><span class="row-k">STEP_WAIT</span><input class="row-v" type="number" step="0.1" id="STEP_WAIT"></div>
      <div class="row"><span class="row-k">MKT_CD_MS</span><input class="row-v" type="number" id="FIRE_SAME_MKT_COOLDOWN_MS"></div>
      <div class="row"><span class="row-k">API_GAP_MS</span><input class="row-v" type="number" id="FIRE_API_GAP_MS"></div>
    </div>
    <div class="sec">
      <div class="sec-title">Scan / API</div>
      <div class="row"><span class="row-k">SCAN_INT</span><input class="row-v" type="number" id="SCAN_INTERVAL"></div>
      <div class="row"><span class="row-k">PRELOAD_S</span><input class="row-v" type="number" id="PRELOAD_SECS"></div>
      <div class="row"><span class="row-k">POOL_S</span><input class="row-v" type="number" id="POOL_RELOAD_INTERVAL"></div>
      <div class="row"><span class="row-k">KEEPALIVE</span><input class="row-v" type="number" id="KEEPALIVE_INTERVAL"></div>
    </div>
    <div class="sec">
      <div class="sec-title">Quick Actions</div>
      <div style="padding:3px 12px;display:flex;flex-direction:column;gap:3px">
        <button class="btn btn-o" style="width:100%;text-align:left;font-size:9px" onclick="setDry(true)">🔒 DRY RUN ON</button>
        <button class="btn btn-r" style="width:100%;text-align:left;font-size:9px" onclick="setDry(false)">🔥 DRY RUN OFF</button>
      </div>
    </div>
    <div class="save-bar">
      <button class="btn btn-g" onclick="saveConfig()">Save</button>
      <button class="btn btn-o" onclick="loadConfig()">Reset</button>
      <span class="toast" id="toast">Saved ✓</span>
    </div>
  </div>

  <!-- ─── Main Content ─── -->
  <div class="main">

    <!-- ═══ MARKET TABLE ═══ -->
    <div class="mkt-section" id="mktSection">
      <div class="mkt-header">
        <div style="display:flex;align-items:center;gap:12px;padding:4px 8px;">
          <label style="font-size:10px;color:var(--t2);cursor:pointer;display:flex;align-items:center;gap:4px">
            <input type="checkbox" id="chkOpen" checked onchange="renderMarkets()"> Open
          </label>
          <label style="font-size:10px;color:var(--t2);cursor:pointer;display:flex;align-items:center;gap:4px">
            <input type="checkbox" id="chkClosed" checked onchange="renderMarkets()"> Closed
          </label>
          <span id="xTrackInfo" style="font-size:10px;color:var(--t3)"></span>
        </div>
        <span class="mkt-count" id="mktCount" style="margin-left:8px">0 tracked</span>
        <button class="mkt-toggle" id="mktToggle" onclick="toggleMktPanel()">▼</button>
      </div>
      <div class="mkt-filter">
        <input type="text" placeholder="Filter markets..." id="mktFilter" oninput="renderMarkets()">
        <label><input type="checkbox" id="mktEvOnly" onchange="renderMarkets()"> EV+ only</label>
        <label><input type="checkbox" id="mktNoLine" onchange="renderMarkets()"> NO_LINE</label>
        <span id="sportFilters" style="margin-left:6px">
          <button class="qbtn sport-btn active" data-sport="" onclick="setSport(this,'')">All</button>
          <button class="qbtn sport-btn" data-sport="soccer" onclick="setSport(this,'soccer')">⚽</button>
          <button class="qbtn sport-btn" data-sport="basketball" onclick="setSport(this,'basketball')">🏀</button>
          <button class="qbtn sport-btn" data-sport="cs2" onclick="setSport(this,'cs2')">CS2</button>
          <button class="qbtn sport-btn" data-sport="dota" onclick="setSport(this,'dota')">Dota</button>
          <button class="qbtn sport-btn" data-sport="lol" onclick="setSport(this,'lol')">LoL</button>
          <button class="qbtn sport-btn" data-sport="valorant" onclick="setSport(this,'valorant')">Val</button>
        </span>
      </div>
      <div class="mkt-scroll">
        <table class="mt"><thead><tr>
          <th onclick="sortMkt('n')">Market</th>
          <th onclick="sortMkt('lg')">League</th>
          <th>Line</th>
          <th onclick="sortMkt('b')" class="sorted tc">EV</th>
          <th class="tc">Etop</th><th class="tc">PS Fair</th>
          <th onclick="sortMkt('pa')" class="tc">PS Age</th>
          <th onclick="sortMkt('s')" class="tc">Time</th>
          <th class="tc">Fired</th>
          <th class="tc">Status</th>
        </tr></thead><tbody id="mb"></tbody></table>
      </div>
    </div>

    <!-- ═══ RESIZE HANDLE ═══ -->
    <div class="resize-handle" id="resizeHandle"></div>

    <!-- ═══ LOG VIEWER ═══ -->
    <div class="log-section">
      <div class="log-toolbar" id="logToolbar">
        <!-- Phase buttons injected by JS -->
        <input type="text" class="log-search" placeholder="Search log..." id="logSearch" oninput="renderLog()">
      </div>
      <div class="log-content" id="logContent"></div>
    </div>

    <!-- ═══ COMMAND CONSOLE ═══ -->
    <div class="console" id="consoleSection">
      <div class="console-header" onclick="toggleConsole()">
        <h3>⌘ Console</h3>
        <button class="console-toggle" id="consoleToggle">▲</button>
      </div>
      <div class="quick-row">
        <button class="qbtn resub" onclick="qcmd('resub esports')">⚡ Esports</button>
        <button class="qbtn resub" onclick="qcmd('resub soccer')">⚡ Soccer</button>
        <button class="qbtn resub" onclick="qcmd('resub basketball')">⚡ Bball</button>
        <button class="qbtn" onclick="qcmd('check_etop all')">📋 Etop</button>
        <button class="qbtn" onclick="qcmd('check_ps')">📋 PS</button>
        <button class="qbtn" onclick="qcmd('status')">ℹ Status</button>
        <button class="qbtn" onclick="qcmd('help')">? Help</button>
      </div>
      <div class="cmd-row">
        <input class="cmd-input" id="cmdInput" placeholder="Type command..." autocomplete="off"
               onkeydown="cmdKey(event)">
        <button class="cmd-run" onclick="runCmd()">Run</button>
      </div>
      <div class="cmd-output" id="cmdOutput"></div>
    </div>

  </div>
</div>

<script>
// ═══════════════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════════════
const CFG_FIELDS = ['MIN_EV','PHASE1_EV','PHASE2_EV','PHASE3_EV','TRIGGER_SECS','MIN_RAW_POOL','EXTENSION_SECS','MAX_ITEMS',
                    'ITEM_VALUE','STEP_WAIT','SCAN_INTERVAL','PRELOAD_SECS',
                    'POOL_RELOAD_INTERVAL','KEEPALIVE_INTERVAL',
                    'FIRE_SAME_MKT_COOLDOWN_MS','FIRE_API_GAP_MS'];

let lastMkts = [];
let mktTimers = {};  // mid → {remain, at} for smooth countdown
let lastRemainAt = Date.now() / 1000;
let logLines = [];
let activePhases = new Set();
let cmdHistory = [];
let cmdIdx = -1;
let mktSort = 'n';     // sort by name (stable) — not by EV (jumpy)
let activeSport = '';
function setSport(el, sport) {
  activeSport = sport;
  document.querySelectorAll('.sport-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  renderMarkets();
}
let mktDir = 1;        // ascending
let userSorted = false; // true after user clicks a column header

// ═══════════════════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════════════════
// PHASE FILTER DEFINITIONS
// ═══════════════════════════════════════════════════════════════════════
const PHASES = {
  discovery: {
    label: 'Discovery', color: '#448aff',
    re: /\[LISTING\]|\[MATCH\]|\[REST_MATCH\]|\[EVIDENCE\]|DISCOVERED|UNMATCHED|\[PS_FETCH\]|\[PS_SEARCH\]|\[REJECTED\]|\[KILLS_EID\]|\[PAIR\]|\[LINE\]/
  },
  monitor: {
    label: 'Monitor', color: '#b388ff',
    re: /\| MONITOR|\| PREFIRE|\| FIRE_ZONE|\[EV\]|\[MONITOR\]|\[TIMING\]/
  },
  firegate: {
    label: 'Fire Gate', color: '#ffc107',
    re: /\[PREFIRE\]|\[AOS\]|ALL_GATES_PASS|GHOST_KILLED|GHOST_ODDS/
  },
  fire: {
    label: 'Fire', color: '#00e676',
    re: /\[FIRE\]|\[QUEUE\]|FIRE!|COMPLETE|PLACED/
  },
  session: {
    label: 'Session', color: '#00e5ff',
    re: /\[SESSION\]|\[WS\]|\[BROWSE\]|keepalive|FULL_ODDS|std_store=|\[RESUB\]/
  },
  errors: {
    label: 'Errors', color: '#ff3d57',
    re: /\[ERROR\]|\[WARN\]/
  },
};

// ═══════════════════════════════════════════════════════════════════════
// INIT — Build phase buttons
// ═══════════════════════════════════════════════════════════════════════
(function initPhaseButtons() {
  const toolbar = document.getElementById('logToolbar');
  const search = toolbar.querySelector('.log-search');
  let html = '';
  for (const [key, ph] of Object.entries(PHASES)) {
    html += '<button class="phase-btn" data-phase="'+key+'" onclick="togglePhase(\''+key+'\')">' +
      '<span class="dot" style="background:'+ph.color+'"></span>' +
      ph.label +
      ' <span class="cnt" id="pc_'+key+'">0</span></button>';
  }
  toolbar.insertAdjacentHTML('afterbegin', html);
})();

// ═══════════════════════════════════════════════════════════════════════
// PHASE FILTER LOGIC
// ═══════════════════════════════════════════════════════════════════════
function togglePhase(phase) {
  if (activePhases.has(phase)) activePhases.delete(phase);
  else activePhases.add(phase);
  document.querySelectorAll('.phase-btn').forEach(b => {
    const p = b.dataset.phase;
    if (activePhases.has(p)) {
      b.classList.add('on');
      b.style.borderColor = PHASES[p].color;
      b.style.color = PHASES[p].color;
      b.style.background = PHASES[p].color + '15';
    } else {
      b.classList.remove('on');
      b.style.borderColor = '';
      b.style.color = '';
      b.style.background = '';
    }
  });
  renderLog();
}

function updatePhaseCounts() {
  for (const [key, ph] of Object.entries(PHASES)) {
    const cnt = logLines.filter(l => ph.re.test(l)).length;
    const el = document.getElementById('pc_' + key);
    if (el) el.textContent = cnt;
  }
}

// ═══════════════════════════════════════════════════════════════════════
// LOG RENDERING
// ═══════════════════════════════════════════════════════════════════════
function getLineClass(l) {
  if (/FIRE!|COMPLETE|ALL_GATES/.test(l)) return 'l-fire';
  if (/\[ERROR\]/.test(l)) return 'l-error';
  if (/\[WARN\]/.test(l)) return 'l-warn';
  if (/\[AOS\]|\[PREFIRE\]/.test(l)) return 'l-gate';
  if (/\[WS\]|\[SESSION\]/.test(l)) return 'l-session';
  if (/DISCOVERED|\[MATCH\]|\[EVIDENCE\]/.test(l)) return 'l-discovery';
  if (/MONITOR|PREFIRE|FIRE_ZONE/.test(l)) return 'l-monitor';
  return '';
}

function renderLog() {
  let lines = logLines;

  // Phase filter: show lines matching ANY active phase
  if (activePhases.size > 0) {
    lines = lines.filter(l => {
      for (const p of activePhases) {
        if (PHASES[p].re.test(l)) return true;
      }
      return false;
    });
  }

  // Text search (stacks on phase filter)
  const q = document.getElementById('logSearch').value.toLowerCase();
  if (q) lines = lines.filter(l => l.toLowerCase().includes(q));

  const box = document.getElementById('logContent');
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 50;

  box.innerHTML = lines.map(l => {
    const cls = getLineClass(l);
    return '<div class="log-line ' + cls + '"><span class="log-ts">' +
      esc(l.substring(0, 10)) + '</span><span class="log-msg">' +
      esc(l.substring(10)) + '</span></div>';
  }).join('');

  if (atBottom) box.scrollTop = box.scrollHeight;
}

// ═══════════════════════════════════════════════════════════════════════
// MARKET TABLE
// ═══════════════════════════════════════════════════════════════════════
function evF(v) {
  if (v === null || v === undefined) return '<span class="ev-no">N/A</span>';
  if (v >= 5) return '<span class="evh">+' + v.toFixed(1) + '%</span>';
  if (v > 0) return '<span class="evp">+' + v.toFixed(1) + '%</span>';
  return '<span class="evn">' + v.toFixed(1) + '%</span>';
}
function secF(s) {
  if (s <= 0) return '<span class="r">locked</span>';
  if (s <= 60) return '<span class="sec-h">' + s + 's</span>';
  if (s <= 300) return '<span class="sec-w">' + Math.floor(s/60) + 'm' + ('0'+s%60).slice(-2) + 's</span>';
  return '<span class="sec-c">' + Math.floor(s/60) + 'm' + ('0'+s%60).slice(-2) + 's</span>';
}
function stF(s) {
  if (!s) return '';
  if (s.includes('FIRE!'))      return '<span class="badge b-fire">FIRE!</span>';
  if (s.includes('FIRE_ZONE'))  return '<span class="badge b-fire">FIRE ZONE</span>';
  if (s === 'PREFIRE')          return '<span class="badge b-ext">PREFIRE</span>';
  if (s === 'MONITOR')          return '<span class="badge b-watch">MONITOR</span>';
  if (s === 'NO_PS_LINE')       return '<span class="badge b-nodata">NO LINE</span>';
  if (s === 'CANCELLED')       return '<span class="badge" style="background:#ff572218;color:#ff5722">CANCELLED</span>';
  if (s === 'LOCKED')          return '<span class="badge" style="background:#ff3d5718;color:#ff3d57">CLOSED</span>';
  if (s === 'UNMATCHED')        return '<span class="badge" style="background:#f59e0b18;color:#f59e0b">UNMATCHED</span>';
  if (s === 'DROPPED')          return '<span class="badge" style="background:#44444418;color:#888">DROPPED</span>';
  if (s === 'APPROACHING')      return '<span class="badge b-watch">APPROACH</span>';
  if (s === 'DISCOVERED')       return '<span class="badge b-disc">DISC</span>';
  return '<span class="badge b-watch">' + s.substring(0, 10) + '</span>';
}
function sortMkt(col) {
  if (mktSort === col) mktDir *= -1; else { mktSort = col; mktDir = -1; }
  userSorted = true;
  renderMarkets();
}
function smoothTime(m) {
  const t = mktTimers[m.mid];
  if (!t) return m.s;
  const elapsed = (Date.now() - t.at) / 1000;
  return Math.max(0, Math.round(t.remain - elapsed));
}


function renderMarkets() {
  const renderNow = Date.now() / 1000;
  const nowSec = renderNow;
  const liveRemain = (m) => {
    if (m.s <= 0) return 0;
    const timer = mktTimers[m.mid];
    if (!timer) return m.s;
    return Math.max(0, timer.remain - (renderNow - timer.at / 1000));
  };
  let mkts = lastMkts.map(m => ({...m, _t: Math.max(0, Math.round(liveRemain(m)))}));

  const showOpen = document.getElementById('chkOpen')?.checked ?? true;
  const showClosed = document.getElementById('chkClosed')?.checked ?? true;
  mkts = mkts.filter(m => {
    if (m.st === 'CLOSED') return showClosed;
    return showOpen;
  });

  const filt = document.getElementById('mktFilter').value.toLowerCase();
  const evOnly = document.getElementById('mktEvOnly').checked;
  const noLine = document.getElementById('mktNoLine').checked;
  if (filt) mkts = mkts.filter(m => m.n.toLowerCase().includes(filt));
  if (evOnly) mkts = mkts.filter(m => m.b > 0);
  if (!noLine) mkts = mkts.filter(m => !m.no_line || m.pf === '–');
  if (activeSport) mkts = mkts.filter(m => m.game === activeSport);
  // Only sort if user clicked a column header — otherwise keep insertion order (stable)
  if (userSorted) {
    mkts.sort((a, b) => {
      let va = a[mktSort], vb = b[mktSort];
      if(mktSort==='s'){return mktDir*(liveRemain(b)-liveRemain(a));}
      if (typeof va === 'string') return mktDir * va.localeCompare(vb);
      return mktDir * ((vb || 0) - (va || 0));
    });
  }
  const openCount = lastMkts.filter(m => liveRemain(m) > 0).length;
  const closedCount = lastMkts.length - openCount;
  const infoEl = document.getElementById('xTrackInfo');
  if (infoEl) infoEl.textContent = openCount + ' open / ' + closedCount + ' closed / ' + lastMkts.length + ' total';
  document.getElementById('mktCount').textContent = mkts.length + ' / ' + lastMkts.length + ' tracked';
  const _html = mkts.map(m =>
    '<tr><td class="name" title="' + esc(m.n) + '">' + esc(m.n) + '</td><td class="sec-c" style="white-space:nowrap;max-width:140px;overflow:hidden;text-overflow:ellipsis" title="' + esc(m.lg || '') + '">' + esc(m.lg || '–') + '</td><td style="white-space:nowrap"><span class="sec-c">' + esc(m.ml || '') + '</span></td><td class="tc">' +
    (!m.ps ? '<span class="ev-no">–</span>' : m.e1 == null ? '<span class="ev-no">NO LINE</span>' : evF(m.e1) + ' / ' + evF(m.e2)) + '</td><td class="sec-c tc">' +
    (function(et, e1, e2, b) {
      if (!et || et === '–') return '–';
      var parts = et.split('/');
      if (parts.length !== 2) return et;
      var h = parts[0], a = parts[1];
      if (Math.abs(b - e1) < Math.abs(b - e2)) return '<span class="g">[' + h + ']</span>/' + a;
      if (Math.abs(b - e2) < Math.abs(b - e1)) return h + '/<span class="g">[' + a + ']</span>';
      return et;
    })(m.etop, m.e1, m.e2, m.b) + '</td><td class="sec-c tc">' + (m.pf || '–') + '</td><td class="tc">' +
    (m.pa > 0 ? '<span data-pa="' + m.pa + '" data-pa-at="' + lastRemainAt + '"></span>' : '–') +
    '</td><td class="tc" data-remain="' + (mktTimers[m.mid]?.remain ?? m._t) + '" data-at="' + renderNow + '"></td><td class="tc">' +
    (m.inv_value > 0 ? '<span class="g">' + m.inv_value.toFixed(1) + 'g</span>' : '<span class="sec-c">–</span>') +
    '</td><td class="tc">' + (function(st, t) {
      if (st === 'CANCELLED') return '<span class="badge" style="background:#ff572218;color:#ff5722">CANCELLED</span>';
      if (st === 'CLOSED') return '<span class="badge" style="background:#ff3d5718;color:#ff3d57">CLOSED</span>';      if (t <= 0) return '<span class="badge" style="background:#ff3d5718;color:#ff3d57">CLOSED</span>';
      if (!m.ps) return '<span class="badge" style="background:#f59e0b18;color:#f59e0b">UNMATCHED</span>';
      if (t <= 30) return '<span class="badge b-fire">P3 FIRE</span>';
      if (t <= 60) return '<span class="badge b-ext">P2 FIRE</span>';
      if (t <= 90) return '<span class="badge" style="background:#2196f318;color:#2196f3">P1 FIRE</span>';
      if (t <= 130) return '<span class="badge b-ext">PREFIRE</span>';
      return '<span class="badge b-watch">MONITOR</span>';
    })(m.st, m._t) + '</td></tr>'
  ).join('');
  if (_html !== window._lastRowsHtml) {
      window._lastRowsHtml = _html;
      document.getElementById('mb').innerHTML = _html;
  }
  fillTimeCells();
}

// ═══════════════════════════════════════════════════════════════════════
// COMMAND CONSOLE
// ═══════════════════════════════════════════════════════════════════════
function cmdKey(e) {
  if (e.key === 'Enter') { e.preventDefault(); runCmd(); }
  else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (cmdHistory.length && cmdIdx > 0) {
      cmdIdx--;
      document.getElementById('cmdInput').value = cmdHistory[cmdIdx];
    } else if (cmdHistory.length && cmdIdx === -1) {
      cmdIdx = cmdHistory.length - 1;
      document.getElementById('cmdInput').value = cmdHistory[cmdIdx];
    }
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (cmdIdx >= 0 && cmdIdx < cmdHistory.length - 1) {
      cmdIdx++;
      document.getElementById('cmdInput').value = cmdHistory[cmdIdx];
    } else {
      cmdIdx = -1;
      document.getElementById('cmdInput').value = '';
    }
  }
}

function qcmd(cmd) { document.getElementById('cmdInput').value = cmd; runCmd(); }

async function runCmd() {
  const input = document.getElementById('cmdInput');
  const cmd = input.value.trim();
  if (!cmd) return;
  cmdHistory.push(cmd);
  cmdIdx = -1;
  input.value = '';

  const out = document.getElementById('cmdOutput');
  out.innerHTML += '<div class="cmd-entry"><span class="cmd-prompt">❯</span> <span class="cmd-text">' + esc(cmd) + '</span></div>';
  const loadId = 'ld_' + Date.now();
  out.innerHTML += '<div class="cmd-loading" id="' + loadId + '">running...</div>';
  out.scrollTop = out.scrollHeight;

  try {
    const resp = await fetch('/api/command', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd})
    });
    const data = await resp.json();
    const el = document.getElementById(loadId);
    if (el) el.remove();
    const cls = data.ok ? 'cmd-ok' : 'cmd-fail';
    out.innerHTML += '<div class="cmd-result ' + cls + '"><pre>' + esc(data.msg || 'No response') + '</pre></div>';
  } catch(e) {
    const el = document.getElementById(loadId);
    if (el) el.remove();
    out.innerHTML += '<div class="cmd-result cmd-fail"><pre>Error: ' + esc(e.message) + '</pre></div>';
  }
  out.scrollTop = out.scrollHeight;
}

// ═══════════════════════════════════════════════════════════════════════
// PANEL TOGGLE
// ═══════════════════════════════════════════════════════════════════════
function toggleMktPanel() {
  const s = document.getElementById('mktSection');
  s.classList.toggle('collapsed');
  document.getElementById('mktToggle').textContent = s.classList.contains('collapsed') ? '▶' : '▼';
  if (!s.classList.contains('collapsed')) s.style.height = '35vh';
}
function toggleConsole() {
  const s = document.getElementById('consoleSection');
  s.classList.toggle('collapsed');
  document.getElementById('consoleToggle').textContent = s.classList.contains('collapsed') ? '▲' : '▼';
}

// ═══════════════════════════════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════════════════════════════
async function loadConfig() {
  try {
    const c = await (await fetch('/api/config')).json();
    CFG_FIELDS.forEach(f => { const e = document.getElementById(f); if (e) e.value = c[f] ?? ''; });
  } catch(e) {}
}
async function saveConfig() {
  const c = await (await fetch('/api/config')).json();
  CFG_FIELDS.forEach(f => { const e = document.getElementById(f); if (e && e.value !== '') c[f] = parseFloat(e.value); });
  await fetch('/api/config', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(c) });
  const t = document.getElementById('toast'); t.classList.add('on'); setTimeout(() => t.classList.remove('on'), 1500);
}
async function setDry(v) {
  try {
    const c = await (await fetch('/api/config')).json();
    c.DRY_RUN = v;
    await fetch('/api/config', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(c) });
    alert(v ? 'DRY RUN enabled — bot will NOT fire' : 'DRY RUN disabled — bot is LIVE');
  } catch(e) { alert('Failed: ' + e.message); }
}

// ═══════════════════════════════════════════════════════════════════════
// DATA REFRESH
// ═══════════════════════════════════════════════════════════════════════
let _statusBusy = false;
async function refreshStatus() {
  if (_statusBusy) return;
  _statusBusy = true;
  try {
    const s = await (await fetch('/api/status?_=' + Date.now())).json();
    // WS indicator
    const d = document.getElementById('wsDot');
    const lb = document.getElementById('wsLabel');
    d.className = 'ws-dot ' + (s.ws ? 'on' : 'off');
    lb.textContent = s.ws ? 'LIVE' : 'DOWN';
    lb.style.color = s.ws ? 'var(--g)' : 'var(--r)';
    // PID + uptime
    document.getElementById('botPid').textContent = s.pid ? 'PID ' + s.pid : '';
    if (s.bot_start) { window._botStart = s.bot_start; } else if (!s.pid) { window._botStart = null; }
    // Session uptime/downtime badges
    const st = s.session_tracker;
    const upEl = document.getElementById('sessUp');
    const dnEl = document.getElementById('sessDown');
    if (st) {
        upEl.textContent = '▲ ' + st.ws_uptime + ' (' + st.ws_pct + '%)';
        upEl.style.display = '';
        const hasDrops = st.ws_reconnects > 0 || !st.ws_connected;
        dnEl.textContent = '▼ ' + (st.ws_downtime || st.last_drop_ago) + (st.ws_reconnects > 0 ? ' ×' + st.ws_reconnects : '');
        dnEl.style.display = hasDrops ? '' : 'none';
    }
    // Stats
    document.getElementById('xTrack').textContent = s.tracking;
    document.getElementById('xEvp').textContent = s.ev_pos;
    document.getElementById('xFires').textContent = s.fires;
    document.getElementById('xWarns').textContent = s.warns;
    document.getElementById('xErr').textContent = s.errors;
    document.getElementById('xErr').style.color = s.errors > 0 ? 'var(--r)' : 'var(--t2)';
    document.getElementById('xList').textContent = s.listing;
    document.getElementById('xBagHd').textContent = s.bag_value > 0 ? s.bag_value.toFixed(1) + 'g / ' + s.bag_count : '–';
    // Bus freshness
    const bf = s.bus_freshness || {};
    const bfEl = document.getElementById('busFresh');
    if (bfEl) {
      const rename = {'ps3838':'PS','etop':'ETOP'};
      bfEl.innerHTML = Object.entries(bf).map(([k,v]) => {
        const label = rename[k] || k;
        const age = typeof v === 'number' ? v : parseFloat(v);
        const col = age <= 2 ? '#4ade80' : age <= 5 ? '#facc15' : '#f87171';
        return '<span style="color:' + col + '">' + label + ':' + age.toFixed(1) + 's</span>';
      }).join(' <span style="color:var(--t3)">|</span> ') || 'no bus';
    }
    // Markets come from SSE only — do NOT update lastMkts here to avoid stale-data flicker
    if (!userSorted && lastMkts.length > 0) {
      mktSort = 'b';
      mktDir = -1;
      userSorted = true;
    }
  } catch(e) {}
  _statusBusy = false;
}

async function refreshLog() {
  try {
    const resp = await fetch('/api/log?n=500');
    const data = await resp.json();
    logLines = data.lines || [];
    updatePhaseCounts();
    renderLog();
  } catch(e) {}
}

// ═══════════════════════════════════════════════════════════════════════
// UTILITY
// ═══════════════════════════════════════════════════════════════════════
function esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function updateClock() {
  document.getElementById('clk').textContent = new Date().toLocaleTimeString('en-GB');
  // Uptime countup
  const el = document.getElementById('botUptime');
  if (window._botStart) {
    const secs = Math.floor(Date.now() / 1000 - window._botStart);
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    const hh = String(h).padStart(2,'0');
    const mm = String(m).padStart(2,'0');
    const ss = String(s).padStart(2,'0');
    el.textContent = hh + ':' + mm + ':' + ss;
  } else {
    el.textContent = '';
  }
}


// ═══════════════════════════════════════════════════════════════════════
// RESIZE HANDLE
// ═══════════════════════════════════════════════════════════════════════
(function initResize() {
  const handle = document.getElementById('resizeHandle');
  const mkt = document.getElementById('mktSection');
  let startY, startH, dragging = false;

  handle.addEventListener('mousedown', e => {
    e.preventDefault();
    startY = e.clientY;
    startH = mkt.offsetHeight;
    dragging = true;
    handle.classList.add('dragging');
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const delta = e.clientY - startY;
    const maxH = window.innerHeight * 0.7;
    const newH = Math.max(60, Math.min(startH + delta, maxH));
    mkt.style.height = newH + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();

// ═══════════════════════════════════════════════════════════════════════
// SSE — real-time push from bot pipeline
// ═══════════════════════════════════════════════════════════════════════
let _sseActive = false;

function handleSSEData(s) {
  // Same rendering as refreshStatus but from SSE push
  const d = document.getElementById('wsDot');
  const lb = document.getElementById('wsLabel');
  d.className = 'ws-dot ' + (s.ws ? 'on' : 'off');
  lb.textContent = s.ws ? 'LIVE' : 'DOWN';
  lb.style.color = s.ws ? 'var(--g)' : 'var(--r)';
  // Stats
  const ev_pos = (s.markets || []).filter(m => (m.b || -999) > 0).length;
  const fires = (s.markets || []).filter(m => m.s > 0 && m.s <= 50).length;
  document.getElementById('xTrack').textContent = s.tracked || 0;
  document.getElementById('xEvp').textContent = ev_pos;
  document.getElementById('xFires').textContent = fires;
  document.getElementById('xList').textContent = s.listing || 0;
  document.getElementById('xBagHd').textContent = (s.bag_value || 0) > 0 ? (s.bag_value).toFixed(1) + 'g / ' + s.bag_count : '–';
  // Bus freshness
  const bf = s.bus_freshness || {};
  const bfEl = document.getElementById('busFresh');
  if (bfEl) {
    const rename = {'ps3838':'PS','etop':'ETOP'};
    bfEl.innerHTML = Object.entries(bf).map(([k,v]) => {
      const label = rename[k] || k;
      const age = typeof v === 'number' ? v : parseFloat(v);
      const col = age <= 2 ? '#4ade80' : age <= 5 ? '#facc15' : '#f87171';
      return '<span style="color:' + col + '">' + label + ':' + age.toFixed(1) + 's</span>';
    }).join(' <span style="color:var(--t3)">|</span> ') || 'no bus';
  }
  // SSE indicator
  const sseEl = document.getElementById('sseStatus');
  if (sseEl) { sseEl.textContent = 'SSE ●'; sseEl.style.color = 'var(--g)'; }
  // Markets
  lastMkts = s.markets || [];
  const remainAt = s.ts || (Date.now() / 1000);
  lastRemainAt = remainAt;
  for (const m of lastMkts) {
    mktTimers[m.mid] = { remain: m.s, at: Date.now() };
  }
  renderMarkets();
  if (!userSorted && lastMkts.length > 0) { mktSort = 'b'; mktDir = -1; userSorted = true; }
}

function initSSE() {
  const es = new EventSource('http://localhost:8889/sse');
  es.onmessage = (e) => {
    try { handleSSEData(JSON.parse(e.data)); } catch(err) {}
  };
  es.onopen = () => {
    _sseActive = true;
    const sseEl = document.getElementById('sseStatus');
    if (sseEl) { sseEl.textContent = 'SSE ●'; sseEl.style.color = 'var(--g)'; }
  };
  es.onerror = () => {
    _sseActive = false;
    const sseEl = document.getElementById('sseStatus');
    if (sseEl) { sseEl.textContent = 'SSE ○'; sseEl.style.color = 'var(--r)'; }
    // Reconnect after 3s
    es.close();
    setTimeout(initSSE, 3000);
  };
}

// ═══════════════════════════════════════════════════════════════════════
// BOOT
// ═══════════════════════════════════════════════════════════════════════
loadConfig();
refreshStatus();
refreshLog();
updateClock();
setInterval(updateClock, 1000);
initSSE();
// Slow fallback poll — SSE is primary; this catches session_tracker + warn/error counts
async function pollStatus() { await refreshStatus(); setTimeout(pollStatus, 5000); }
async function pollLog() { await refreshLog(); setTimeout(pollLog, 3000); }
pollStatus();
pollLog();
function fillTimeCells() {
  const now = Date.now() / 1000;
  document.querySelectorAll('[data-remain]').forEach(el => {
    const base = parseFloat(el.dataset.remain);
    const at = parseFloat(el.dataset.at);
    const cur = Math.max(0, base - (now - at));
    const h = Math.floor(cur / 3600);
    const m = Math.floor((cur % 3600) / 60);
    const sec = Math.floor(cur % 60);
    if (cur <= 0) { el.innerHTML = '<span class="r">locked</span>'; } else { el.textContent = h > 0 ? h + 'h' + String(m).padStart(2,'0') + 'm' : m + 'm' + String(sec).padStart(2,'0') + 's'; }
  });
  document.querySelectorAll('[data-pa]').forEach(el => {
    const base = parseFloat(el.dataset.pa);
    const at = parseFloat(el.dataset.paAt);
    const lpa = Math.round(base + (now - at));
    el.textContent = lpa + 's';
    el.className = lpa > 120 ? 'sec-h' : 'sec-c';
  });
}
setInterval(fillTimeCells, 1000);
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP Server
# ═══════════════════════════════════════════════════════════════════════════════

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _j(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html;charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif path == '/api/status':
            self._j(get_status())
        elif path == '/api/config':
            self._j(load_config())
        elif path == '/api/log':
            n = int(qs.get('n', ['500'])[0])
            filt = qs.get('filter', [''])[0]
            self._j({'lines': tail_log(min(n, 1000), filt if filt else None)})
        elif path == '/api/command_log':
            try:
                with open(CMD_LOG_PATH) as f:
                    self._j(json.load(f))
            except Exception:
                self._j([])
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/config':
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            save_config(json.loads(body))
            # Signal bot to hot-reload config via IPC
            try:
                import time as _t
                with open(CMD_IN_PATH, 'w') as _f:
                    json.dump({'cmd': 'reload_config', 'ts': str(_t.time())}, _f)
            except Exception:
                pass
            self._j({'ok': True})
        elif self.path == '/api/command':
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            try:
                cmd = json.loads(body).get('cmd', '').strip()
                result = send_command(cmd)
                self._j(result)
            except Exception as e:
                self._j({'ok': False, 'msg': str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


if __name__ == '__main__':
    s = HTTPServer(('0.0.0.0', 8888), H)
    print(f"\033[92mCleanFlowBot\033[0m Mission Control → http://localhost:8888")
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
        s.server_close()
