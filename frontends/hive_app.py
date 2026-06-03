# hive_app.py — 蜂巢管理前端
# 基于FastAPI的蜂巢系统管理界面，提供目标、Worker、任务的可视化管理

import asyncio
import json
import os
import sys
import time
import sqlite3
import uuid
from typing import Set, Optional
from contextlib import contextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- 数据库 ----
DEFAULT_DB = "hive_bbs.db"

@contextmanager
def get_db(db_path=None):
    conn = sqlite3.connect(db_path or DEFAULT_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db(db_path=None):
    with get_db(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS goals (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT,
            status TEXT DEFAULT 'open', created_at REAL, updated_at REAL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS workers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, capabilities TEXT,
            status TEXT DEFAULT 'idle', last_seen REAL, created_at REAL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, worker_id TEXT,
            title TEXT NOT NULL, description TEXT, status TEXT DEFAULT 'pending',
            result TEXT, created_at REAL, updated_at REAL,
            FOREIGN KEY(goal_id) REFERENCES goals(id),
            FOREIGN KEY(worker_id) REFERENCES workers(id))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_worker ON tasks(worker_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")

def now(): return time.time()
def short_id(): return uuid.uuid4().hex[:8]

# ---- App ----
app = FastAPI(title="Hive Management")
ws_clients: Set[WebSocket] = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None

async def broadcast(payload: dict):
    dead = []
    for ws in list(ws_clients):
        try: await ws.send_json(payload)
        except: dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)

def schedule_broadcast(payload: dict):
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(payload), main_loop)

def snapshot():
    with get_db() as db:
        goals = [dict(r) for r in db.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()]
        workers = [dict(r) for r in db.execute("SELECT * FROM workers ORDER BY last_seen DESC").fetchall()]
        tasks = [dict(r) for r in db.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 200").fetchall()]
        stats = {
            "goals": {s: db.execute(f"SELECT COUNT(*) c FROM goals WHERE status='{s}'").fetchone()["c"] for s in ("open","in_progress","completed","failed")},
            "workers": {s: db.execute(f"SELECT COUNT(*) c FROM workers WHERE status='{s}'").fetchone()["c"] for s in ("idle","busy","offline")},
            "tasks": {s: db.execute(f"SELECT COUNT(*) c FROM tasks WHERE status='{s}'").fetchone()["c"] for s in ("pending","in_progress","completed","failed")}
        }
    return {"stats": stats, "goals": goals, "workers": workers, "tasks": tasks}

def push_update():
    schedule_broadcast({"type": "update", **snapshot()})

# ---- HTML ----
HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hive Management</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Microsoft YaHei',sans-serif;background:#1a1a2e;color:#e0e0e0}
.ctn{max-width:1400px;margin:0 auto;padding:20px}
h1{color:#e94560;font-size:28px;margin-bottom:20px;text-align:center}
.dash{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:15px;margin-bottom:25px}
.card{background:#16213e;border-radius:10px;padding:18px;border-left:4px solid #0f3460}
.card h3{color:#e94560;margin-bottom:10px;font-size:14px}
.sg{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;text-align:center}
.sv{font-size:22px;font-weight:bold;color:#00d2ff}.sl{font-size:11px;color:#888}
.sec{background:#16213e;border-radius:10px;padding:18px;margin-bottom:20px}
.sec h2{color:#e94560;margin-bottom:12px;font-size:18px;display:flex;justify-content:space-between;align-items:center}
.sec h2 button{background:#0f3460;color:#e0e0e0;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:13px}
.sec h2 button:hover{background:#e94560}
table{width:100%;border-collapse:collapse}
th,td{padding:10px 8px;text-align:left;border-bottom:1px solid #0f3460;font-size:13px}
th{background:#0f3460;color:#e94560;font-weight:bold;position:sticky;top:0}
tr:hover{background:#1a1a3e}
.st{display:inline-block;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:bold}
.s-open{background:#00d2ff;color:#1a1a2e}.s-in_progress{background:#ffc107;color:#1a1a2e}
.s-completed{background:#28a745;color:#fff}.s-failed{background:#dc3545;color:#fff}
.s-idle{background:#6c757d;color:#fff}.s-busy{background:#ffc107;color:#1a1a2e}
.s-offline{background:#dc3545;color:#fff}.s-pending{background:#6c757d;color:#fff}
.btn{padding:4px 10px;border:none;border-radius:3px;cursor:pointer;font-size:11px;margin-right:4px}
.bp{background:#0f3460;color:#e0e0e0}.bp:hover{background:#e94560}
.bs{background:#28a745;color:#fff}.bd{background:#dc3545;color:#fff}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.8);z-index:1000}
.mc{background:#16213e;margin:8% auto;padding:25px;width:90%;max-width:500px;border-radius:10px}
.mh{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
.mh h2{color:#e94560;margin:0;font-size:18px}
.close{color:#888;font-size:26px;cursor:pointer}.close:hover{color:#e94560}
.fg{margin-bottom:12px}
.fg label{display:block;margin-bottom:4px;color:#888;font-size:13px}
.fg input,.fg textarea,.fg select{width:100%;padding:8px;background:#1a1a2e;border:1px solid #0f3460;color:#e0e0e0;border-radius:4px;font-size:13px}
.fg textarea{height:80px;resize:vertical}
.log{background:#1a1a2e;padding:10px;border-radius:5px;max-height:150px;overflow-y:auto;font-family:monospace;font-size:11px}
.log-entry{margin-bottom:3px}.log-time{color:#888}
.rf{position:fixed;bottom:20px;right:20px;background:#e94560;color:#fff;border:none;width:45px;height:45px;border-radius:50%;font-size:18px;cursor:pointer;box-shadow:0 4px 10px rgba(0,0,0,.3)}
.rf:hover{background:#ff6b6b}
.empty{text-align:center;padding:30px;color:#888}
</style></head><body>
<div class="ctn">
<h1>Hive Management System</h1>
<div class="dash">
  <div class="card"><h3>Goals</h3><div class="sg">
    <div><div class="sv" id="g-o">0</div><div class="sl">Open</div></div>
    <div><div class="sv" id="g-p">0</div><div class="sl">In Progress</div></div>
    <div><div class="sv" id="g-c">0</div><div class="sl">Completed</div></div>
    <div><div class="sv" id="g-f">0</div><div class="sl">Failed</div></div>
  </div></div>
  <div class="card"><h3>Workers</h3><div class="sg">
    <div><div class="sv" id="w-i">0</div><div class="sl">Idle</div></div>
    <div><div class="sv" id="w-b">0</div><div class="sl">Busy</div></div>
    <div><div class="sv" id="w-o">0</div><div class="sl">Offline</div></div>
    <div></div>
  </div></div>
  <div class="card"><h3>Tasks</h3><div class="sg">
    <div><div class="sv" id="t-p">0</div><div class="sl">Pending</div></div>
    <div><div class="sv" id="t-ip">0</div><div class="sl">In Progress</div></div>
    <div><div class="sv" id="t-c">0</div><div class="sl">Completed</div></div>
    <div><div class="sv" id="t-f">0</div><div class="sl">Failed</div></div>
  </div></div>
</div>
<div class="sec"><h2>Goals <button onclick="showM('gm')">+ New Goal</button></h2>
<table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead><tbody id="gt"></tbody></table></div>
<div class="sec"><h2>Workers <button onclick="showM('wm')">+ Register</button></h2>
<table><thead><tr><th>ID</th><th>Name</th><th>Capabilities</th><th>Status</th><th>Last Seen</th><th>Actions</th></tr></thead><tbody id="wt"></tbody></table></div>
<div class="sec"><h2>Tasks <button onclick="showM('tm')">+ New Task</button></h2>
<table><thead><tr><th>ID</th><th>Title</th><th>Goal</th><th>Worker</th><th>Status</th><th>Result</th><th>Actions</th></tr></thead><tbody id="tt"></tbody></table></div>
<div class="sec"><h2>System Log</h2><div class="log" id="slog"></div></div>
</div>
<!-- Modals -->
<div id="gm" class="modal"><div class="mc"><div class="mh"><h2>New Goal</h2><span class="close" onclick="hideM('gm')">&times;</span></div>
<form id="gf"><div class="fg"><label>Title</label><input id="g-ti" required></div><div class="fg"><label>Description</label><textarea id="g-de"></textarea></div><button type="submit" class="btn bp">Create</button></form></div></div>
<div id="wm" class="modal"><div class="mc"><div class="mh"><h2>Register Worker</h2><span class="close" onclick="hideM('wm')">&times;</span></div>
<form id="wf"><div class="fg"><label>Name</label><input id="w-na" required></div><div class="fg"><label>Capabilities (comma separated)</label><input id="w-ca" placeholder="coding,testing"></div><button type="submit" class="btn bp">Register</button></form></div></div>
<div id="tm" class="modal"><div class="mc"><div class="mh"><h2>New Task</h2><span class="close" onclick="hideM('tm')">&times;</span></div>
<form id="tf"><div class="fg"><label>Goal</label><select id="t-go" required></select></div><div class="fg"><label>Title</label><input id="t-ti" required></div><div class="fg"><label>Description</label><textarea id="t-de"></textarea></div><div class="fg"><label>Assign Worker (optional)</label><select id="t-wo"><option value="">None</option></select></div><button type="submit" class="btn bp">Create</button></form></div></div>
<button class="rf" onclick="load()">REF</button>
<script>
let logs=[];
const esc=s=>s?s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'):'';
const fmt=t=>t?new Date(t*1000).toLocaleString():'-';
function addLog(m){logs.unshift({t:new Date().toLocaleTimeString(),m});if(logs.length>50)logs.pop();renderLogs();}
function renderLogs(){document.getElementById('slog').innerHTML=logs.map(l=>`<div class="log-entry"><span class="log-time">[${l.t}]</span> ${esc(l.m)}</div>`).join('');}
function showM(id){document.getElementById(id).style.display='block';}
function hideM(id){document.getElementById(id).style.display='none';}
let goals=[],workers=[];
function updateUI(d){
  const s=d.stats;
  document.getElementById('g-o').textContent=s.goals.open||0;
  document.getElementById('g-p').textContent=s.goals.in_progress||0;
  document.getElementById('g-c').textContent=s.goals.completed||0;
  document.getElementById('g-f').textContent=s.goals.failed||0;
  document.getElementById('w-i').textContent=s.workers.idle||0;
  document.getElementById('w-b').textContent=s.workers.busy||0;
  document.getElementById('w-o').textContent=s.workers.offline||0;
  document.getElementById('t-p').textContent=s.tasks.pending||0;
  document.getElementById('t-ip').textContent=s.tasks.in_progress||0;
  document.getElementById('t-c').textContent=s.tasks.completed||0;
  document.getElementById('t-f').textContent=s.tasks.failed||0;
  goals=d.goals;workers=d.workers;
  document.getElementById('gt').innerHTML=goals.length?goals.map(g=>`<tr><td>${g.id}</td><td>${esc(g.title)}</td><td><span class="st s-${g.status}">${g.status}</span></td><td>${fmt(g.created_at)}</td><td>${g.status==='open'?`<button class="btn bp" onclick="updGoal('${g.id}','in_progress')">Start</button>`:g.status==='in_progress'?`<button class="btn bs" onclick="updGoal('${g.id}','completed')">Complete</button>`:''}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">No goals</td></tr>';
  document.getElementById('wt').innerHTML=workers.length?workers.map(w=>`<tr><td>${w.id}</td><td>${esc(w.name)}</td><td>${esc(w.capabilities||'-')}</td><td><span class="st s-${w.status}">${w.status}</span></td><td>${fmt(w.last_seen)}</td><td>${w.status==='idle'?`<button class="btn bp" onclick="updWorker('${w.id}','busy')">Set Busy</button>`:w.status==='busy'?`<button class="btn bs" onclick="updWorker('${w.id}','idle')">Set Idle</button>`:''}</td></tr>`).join(''):'<tr><td colspan="6" class="empty">No workers</td></tr>';
  document.getElementById('tt').innerHTML=d.tasks.length?d.tasks.map(t=>`<tr><td>${t.id}</td><td>${esc(t.title)}</td><td>${t.goal_id}</td><td>${t.worker_id||'-'}</td><td><span class="st s-${t.status}">${t.status}</span></td><td>${t.result?esc(t.result.substring(0,40)):''}</td><td>${t.status==='pending'?`<button class="btn bp" onclick="claimT('${t.id}')">Claim</button>`:t.status==='in_progress'?`<button class="btn bs" onclick="doneT('${t.id}','completed')">Done</button> <button class="btn bd" onclick="doneT('${t.id}','failed')">Fail</button>`:''}</td></tr>`).join(''):'<tr><td colspan="7" class="empty">No tasks</td></tr>';
  const sel=document.getElementById('t-go');sel.innerHTML=goals.map(g=>`<option value="${g.id}">${esc(g.title)}</option>`).join('');
  const sw=document.getElementById('t-wo');sw.innerHTML='<option value="">None</option>'+workers.filter(w=>w.status==='idle').map(w=>`<option value="${w.id}">${esc(w.name)}</option>`).join('');
}
async function api(url,method='GET',body=null){const o={method,headers:{'Content-Type':'application/json'}};if(body)o.body=JSON.stringify(body);return(await fetch(url,o)).json();}
async function load(){try{const d=await api('/api/snapshot');updateUI(d);addLog('Refreshed');}catch(e){addLog('Error: '+e.message);}}
async function updGoal(id,s){await api(`/api/goal/${id}`,'PUT',{status:s});load();addLog(`Goal ${id} -> ${s}`);}
async function updWorker(id,s){await api(`/api/worker/${id}/status`,'POST',{status:s});load();addLog(`Worker ${id} -> ${s}`);}
async function claimT(id){const w=prompt('Enter Worker ID:');if(w){await api(`/api/task/${id}/claim`,'POST',{worker_id:w});load();addLog(`Task ${id} claimed by ${w}`);}}
async function doneT(id,s){const r=prompt('Enter result:');if(r!==null){await api(`/api/task/${id}/complete`,'POST',{result:r,status:s});load();addLog(`Task ${id} ${s}`);}}
document.getElementById('gf').onsubmit=async e=>{e.preventDefault();await api('/api/goal','POST',{title:document.getElementById('g-ti').value,description:document.getElementById('g-de').value});hideM('gm');e.target.reset();load();addLog('Goal created');};
document.getElementById('wf').onsubmit=async e=>{e.preventDefault();const caps=document.getElementById('w-ca').value.split(',').map(s=>s.trim()).filter(s=>s);await api('/api/worker/register','POST',{name:document.getElementById('w-na').value,capabilities:caps});hideM('wm');e.target.reset();load();addLog('Worker registered');};
document.getElementById('tf').onsubmit=async e=>{e.preventDefault();await api('/api/task','POST',{goal_id:document.getElementById('t-go').value,title:document.getElementById('t-ti').value,description:document.getElementById('t-de').value,worker_id:document.getElementById('t-wo').value||null});hideM('tm');e.target.reset();load();addLog('Task created');};
let ws=null;
function connectWS(){ws=new WebSocket(`ws://${location.host}/ws`);
ws.onopen=()=>addLog('WS connected');
ws.onmessage=e=>{try{const d=JSON.parse(e.data);if(d.type==='update')updateUI(d);}catch{}};
ws.onclose=()=>{addLog('WS disconnected, reconnecting...');setTimeout(connectWS,3000);};}
connectWS();load();setInterval(load,15000);
</script></body></html>"""

# ---- Startup ----
@app.on_event("startup")
async def on_startup():
    global main_loop
    main_loop = asyncio.get_running_loop()
    init_db()

# ---- Routes ----
@app.get("/", response_class=HTMLResponse)
async def index(): return HTML

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_json({"type": "update", **snapshot()})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)

@app.get("/api/snapshot")
async def api_snapshot(): return snapshot()

@app.get("/api/stats")
async def api_stats(): return snapshot()["stats"]

@app.get("/api/goals")
async def api_goals(status: str = None, limit: int = 50, offset: int = 0):
    with get_db() as db:
        if status:
            rows = db.execute("SELECT * FROM goals WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (status, limit, offset)).fetchall()
        else:
            rows = db.execute("SELECT * FROM goals ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/goal")
async def api_create_goal(title=Body(...), description=Body(""), status=Body("open")):
    gid = short_id(); ts = now()
    with get_db() as db:
        db.execute("INSERT INTO goals(id,title,description,status,created_at,updated_at) VALUES(?,?,?,?,?,?)", (gid, title, description, status, ts, ts))
    push_update()
    return {"id": gid, "status": "created"}

@app.put("/api/goal/{goal_id}")
async def api_update_goal(goal_id: str, title=Body(None), description=Body(None), status=Body(None)):
    with get_db() as db:
        g = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        if not g: raise HTTPException(404, "Goal not found")
        sets, params = [], []
        if title is not None: sets.append("title=?"); params.append(title)
        if description is not None: sets.append("description=?"); params.append(description)
        if status is not None: sets.append("status=?"); params.append(status)
        if sets:
            sets.append("updated_at=?"); params.append(now()); params.append(goal_id)
            db.execute(f"UPDATE goals SET {','.join(sets)} WHERE id=?", params)
    push_update()
    return {"id": goal_id, "status": "updated"}

@app.get("/api/workers")
async def api_workers(status: str = None):
    with get_db() as db:
        if status:
            rows = db.execute("SELECT * FROM workers WHERE status=?", (status,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM workers ORDER BY last_seen DESC").fetchall()
    return [dict(r) for r in rows]

@app.post("/api/worker/register")
async def api_register_worker(name=Body(...), capabilities=Body([])):
    wid = short_id(); ts = now()
    caps = json.dumps(capabilities) if isinstance(capabilities, list) else capabilities
    with get_db() as db:
        db.execute("INSERT INTO workers(id,name,capabilities,status,last_seen,created_at) VALUES(?,?,?,?,?,?)", (wid, name, caps, "idle", ts, ts))
    push_update()
    return {"id": wid, "name": name, "status": "idle"}

@app.post("/api/worker/{worker_id}/status")
async def api_update_worker_status(worker_id: str, status=Body(...)):
    if status not in ("idle", "busy", "offline"):
        raise HTTPException(400, "Invalid status")
    with get_db() as db:
        r = db.execute("UPDATE workers SET status=?,last_seen=? WHERE id=?", (status, now(), worker_id))
        if r.rowcount == 0: raise HTTPException(404, "Worker not found")
    push_update()
    return {"id": worker_id, "status": status}

@app.post("/api/worker/{worker_id}/heartbeat")
async def api_heartbeat(worker_id: str):
    with get_db() as db:
        db.execute("UPDATE workers SET last_seen=? WHERE id=?", (now(), worker_id))
    return {"ok": True}

@app.get("/api/tasks")
async def api_tasks(goal_id: str = None, worker_id: str = None, status: str = None, limit: int = 50):
    with get_db() as db:
        q, p = "SELECT * FROM tasks WHERE 1=1", []
        if goal_id: q += " AND goal_id=?"; p.append(goal_id)
        if worker_id: q += " AND worker_id=?"; p.append(worker_id)
        if status: q += " AND status=?"; p.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"; p.append(limit)
        rows = db.execute(q, p).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/task")
async def api_create_task(goal_id=Body(...), title=Body(...), description=Body(""), worker_id=Body(None)):
    tid = short_id(); ts = now(); st = "pending"
    with get_db() as db:
        if not db.execute("SELECT id FROM goals WHERE id=?", (goal_id,)).fetchone():
            raise HTTPException(404, "Goal not found")
        if worker_id:
            if not db.execute("SELECT id FROM workers WHERE id=?", (worker_id,)).fetchone():
                raise HTTPException(404, "Worker not found")
            st = "in_progress"
            db.execute("UPDATE workers SET status='busy',last_seen=? WHERE id=?", (ts, worker_id))
        db.execute("INSERT INTO tasks(id,goal_id,worker_id,title,description,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   (tid, goal_id, worker_id, title, description, st, ts, ts))
    push_update()
    return {"id": tid, "goal_id": goal_id, "status": st}

@app.post("/api/task/{task_id}/claim")
async def api_claim_task(task_id: str, worker_id=Body(...)):
    ts = now()
    with get_db() as db:
        t = db.execute("SELECT * FROM tasks WHERE id=? AND status='pending'", (task_id,)).fetchone()
        if not t: raise HTTPException(400, "Task not available")
        if not db.execute("SELECT id FROM workers WHERE id=?", (worker_id,)).fetchone():
            raise HTTPException(404, "Worker not found")
        db.execute("UPDATE tasks SET worker_id=?,status='in_progress',updated_at=? WHERE id=?", (worker_id, ts, task_id))
        db.execute("UPDATE workers SET status='busy',last_seen=? WHERE id=?", (ts, worker_id))
    push_update()
    return {"id": task_id, "worker_id": worker_id, "status": "in_progress"}

@app.post("/api/task/{task_id}/complete")
async def api_complete_task(task_id: str, result=Body(...), status=Body("completed")):
    if status not in ("completed", "failed"):
        raise HTTPException(400, "Invalid status")
    ts = now()
    with get_db() as db:
        t = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t: raise HTTPException(404, "Task not found")
        db.execute("UPDATE tasks SET status=?,result=?,updated_at=? WHERE id=?", (status, result, ts, task_id))
        if t["worker_id"]:
            # Check if worker has other in_progress tasks
            cnt = db.execute("SELECT COUNT(*) c FROM tasks WHERE worker_id=? AND status='in_progress' AND id!=?", (t["worker_id"], task_id)).fetchone()["c"]
            if cnt == 0:
                db.execute("UPDATE workers SET status='idle',last_seen=? WHERE id=?", (ts, t["worker_id"]))
    push_update()
    return {"id": task_id, "status": status}

@app.get("/api/readme")
def api_readme():
    return PlainTextResponse("""Hive BBS Management API
=======================
GET  /api/snapshot          Full dashboard snapshot
GET  /api/stats             Stats only
POST /api/goal              Create goal (title, description, status)
PUT  /api/goal/{id}         Update goal
POST /api/worker/register   Register worker (name, capabilities)
POST /api/worker/{id}/status  Update worker status
POST /api/worker/{id}/heartbeat  Worker heartbeat
POST /api/task              Create task (goal_id, title, description, worker_id)
POST /api/task/{id}/claim   Claim task (worker_id)
POST /api/task/{id}/complete  Complete task (result, status)
""")

if __name__ == "__main__":
    import argparse, uvicorn
    p = argparse.ArgumentParser()
    p.add_argument("--cwd")
    p.add_argument("--port", type=int, default=58801)
    p.add_argument("--db", default="hive_bbs.db")
    a = p.parse_args()
    if a.cwd: os.chdir(a.cwd)
    DEFAULT_DB = a.db
    uvicorn.run(app, host="0.0.0.0", port=a.port)
