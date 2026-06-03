# morphling_app.py — Morphling 吸收系统
# 项目能力扫描、分析、吸收与替代管理界面
# 基于 FastAPI + WebSocket + SQLite，与蜂巢系统集成

import asyncio
import ast
import json
import os
import re
import sys
import time
import uuid
import sqlite3
from typing import Set, Optional, List, Dict, Any
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_DB = "morphling.db"
HIVE_API_BASE = os.environ.get("HIVE_API_BASE", "http://127.0.0.1:58801")
SUPPORTED_EXTENSIONS = {".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".h"}
IGNORE_DIRS = {
    "__pycache__", ".git", ".svn", ".hg", "node_modules",
    ".venv", "venv", "env", ".env", ".idea", ".vscode",
    "dist", "build", ".eggs", ".tox", ".codebuddy",
    ".mypy_cache", ".pytest_cache"
}


# ==================== 数据库 ====================

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
        db.execute("""CREATE TABLE IF NOT EXISTS scans (
            id TEXT PRIMARY KEY, project_path TEXT NOT NULL, project_name TEXT NOT NULL,
            status TEXT DEFAULT 'pending', file_count INTEGER DEFAULT 0,
            module_count INTEGER DEFAULT 0, scan_result TEXT, error TEXT,
            created_at REAL, updated_at REAL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS capabilities (
            id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, name TEXT NOT NULL,
            cap_type TEXT NOT NULL, module_path TEXT, description TEXT,
            functions TEXT, classes TEXT, dependencies TEXT,
            complexity TEXT DEFAULT 'medium', status TEXT DEFAULT 'discovered',
            absorb_task_id TEXT, created_at REAL, updated_at REAL,
            FOREIGN KEY(scan_id) REFERENCES scans(id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS absorb_tasks (
            id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, capability_id TEXT NOT NULL,
            strategy TEXT DEFAULT 'integrate', status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0, hive_goal_id TEXT, hive_task_id TEXT,
            result TEXT, error TEXT, created_at REAL, updated_at REAL,
            FOREIGN KEY(scan_id) REFERENCES scans(id),
            FOREIGN KEY(capability_id) REFERENCES capabilities(id))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_caps_scan ON capabilities(scan_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_caps_status ON capabilities(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_absorb_scan ON absorb_tasks(scan_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_absorb_status ON absorb_tasks(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status)")


def now():
    return time.time()


def short_id():
    return uuid.uuid4().hex[:8]


# ==================== 代码分析引擎 ====================

class CodeAnalyzer:

    @staticmethod
    def should_ignore(path: str) -> bool:
        return any(p in IGNORE_DIRS for p in Path(path).parts)

    @staticmethod
    def scan_directory(root_path: str) -> Dict[str, Any]:
        root = Path(root_path)
        if not root.exists():
            raise ValueError(f"Path does not exist: {root_path}")
        file_tree, file_count, extension_stats = [], 0, {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            for f in filenames:
                filepath = os.path.join(dirpath, f)
                rel_path = os.path.relpath(filepath, root)
                ext = os.path.splitext(f)[1].lower()
                if CodeAnalyzer.should_ignore(rel_path):
                    continue
                file_count += 1
                extension_stats[ext] = extension_stats.get(ext, 0) + 1
                file_tree.append({"path": rel_path, "name": f, "ext": ext, "size": os.path.getsize(filepath)})
        return {"file_tree": file_tree, "file_count": file_count, "extension_stats": extension_stats}

    @staticmethod
    def _get_name(node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{CodeAnalyzer._get_name(node.value)}.{node.attr}"
        return "unknown"

    @staticmethod
    def _get_decorator_name(node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{CodeAnalyzer._get_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Call):
            return CodeAnalyzer._get_decorator_name(node.func)
        return "unknown"

    @staticmethod
    def analyze_python_file(filepath: str) -> Dict[str, Any]:
        result = {"functions": [], "classes": [], "imports": [], "constants": [], "decorators_used": []}
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
        except (SyntaxError, UnicodeDecodeError):
            return CodeAnalyzer._regex_analyze(filepath)

        dec_set = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result["functions"].append({
                    "name": node.name,
                    "args": [a.arg for a in node.args.args],
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "decorators": [CodeAnalyzer._get_decorator_name(d) for d in node.decorator_list],
                    "docstring": ast.get_docstring(node) or "",
                    "line": node.lineno,
                })
                for d in node.decorator_list:
                    dec_set.add(CodeAnalyzer._get_decorator_name(d))
            elif isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append({"name": item.name, "is_async": isinstance(item, ast.AsyncFunctionDef)})
                result["classes"].append({
                    "name": node.name,
                    "bases": [CodeAnalyzer._get_name(b) for b in node.bases],
                    "methods": methods,
                    "docstring": ast.get_docstring(node) or "",
                    "line": node.lineno,
                })
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    result["imports"].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    result["imports"].append(f"{mod}.{alias.name}")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        result["constants"].append(target.id)
        result["decorators_used"] = list(dec_set)
        return result

    @staticmethod
    def _regex_analyze(filepath: str) -> Dict[str, Any]:
        result = {"functions": [], "classes": [], "imports": [], "constants": [], "decorators_used": []}
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return result
        for m in re.finditer(r"(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)", content):
            result["functions"].append({"name": m.group(1), "is_async": m.group(0).startswith("async")})
        for m in re.finditer(r"class\s+(\w+)\s*(?:\(([^)]*)\))?", content):
            result["classes"].append({"name": m.group(1), "bases": [b.strip() for b in (m.group(2) or "").split(",") if b.strip()]})
        for m in re.finditer(r"(?:from\s+(\S+)\s+)?import\s+(.+)", content):
            mod = m.group(1) or ""
            for n in [x.strip().split(" as ")[0].strip() for x in m.group(2).split(",")]:
                result["imports"].append(f"{mod}.{n}" if mod else n)
        return result

    @staticmethod
    def analyze_project(root_path: str) -> Dict[str, Any]:
        scan_result = CodeAnalyzer.scan_directory(root_path)
        modules = []
        for file_info in scan_result["file_tree"]:
            if file_info["ext"] != ".py":
                continue
            abs_path = os.path.join(root_path, file_info["path"])
            analysis = CodeAnalyzer.analyze_python_file(abs_path)
            modules.append({
                "path": file_info["path"],
                "name": os.path.splitext(file_info["name"])[0],
                "size": file_info["size"],
                **analysis,
            })
        scan_result["modules"] = modules
        scan_result["module_count"] = len(modules)
        scan_result["capabilities"] = CodeAnalyzer.extract_capabilities(modules)
        return scan_result

    @staticmethod
    def extract_capabilities(modules: List[Dict]) -> List[Dict]:
        capabilities = []
        for mod in modules:
            func_count = len(mod.get("functions", []))
            class_count = len(mod.get("classes", []))
            has_classes, has_functions = class_count > 0, func_count > 0

            if has_classes and has_functions:
                cap_type = "mixed"
                complexity = "high" if (func_count > 10 or class_count > 3) else "medium"
            elif has_classes:
                cap_type = "class_library"
                complexity = "high" if class_count > 5 else "medium"
            elif has_functions:
                cap_type = "function_library"
                complexity = "high" if func_count > 15 else "medium" if func_count > 5 else "low"
            else:
                cap_type = "data_file"
                complexity = "low"

            imports = mod.get("imports", [])
            if any("fastapi" in i or "flask" in i or "django" in i for i in imports):
                cap_type, complexity = "web_service", "high"
            elif any("websocket" in i.lower() for i in imports):
                cap_type, complexity = "websocket_service", "high"
            elif any("sqlite" in i or "sqlalchemy" in i for i in imports):
                cap_type, complexity = "data_layer", "medium"

            func_names = [f["name"] for f in mod.get("functions", [])]
            class_names = [c["name"] for c in mod.get("classes", [])]
            if class_names:
                description = f"Classes: {', '.join(class_names[:5])}"
            elif func_names:
                description = f"Functions: {', '.join(func_names[:5])}"
            else:
                description = f"File: {mod['path']}"

            capabilities.append({
                "name": mod["name"], "cap_type": cap_type, "module_path": mod["path"],
                "description": description, "functions": json.dumps(func_names),
                "classes": json.dumps(class_names), "dependencies": json.dumps(imports),
                "complexity": complexity,
            })
        return capabilities


# ==================== HTML 前端 ====================

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Morphling - Absorption System</title><style>
*{margin:0;padding:0;box-sizing:border-box}body{font-family:'Microsoft YaHei',sans-serif;background:#0d1117;color:#c9d1d9}
.ctn{max-width:1500px;margin:0 auto;padding:20px}
h1{text-align:center;font-size:26px;margin-bottom:6px;background:linear-gradient(90deg,#58a6ff,#bc8cff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{text-align:center;color:#8b949e;font-size:13px;margin-bottom:20px}
.dash{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:22px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.card h3{color:#58a6ff;font-size:13px;margin-bottom:10px}
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(50px,1fr));gap:6px;text-align:center}
.sv{font-size:24px;font-weight:bold;color:#e6edf3}.sl{font-size:11px;color:#8b949e}
.sec{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:18px}
.sec h2{color:#58a6ff;margin-bottom:12px;font-size:16px;display:flex;justify-content:space-between;align-items:center}
table{width:100%;border-collapse:collapse}th,td{padding:9px 10px;text-align:left;border-bottom:1px solid #21262d;font-size:13px}
th{background:#0d1117;color:#58a6ff;font-weight:600;font-size:12px;position:sticky;top:0}tr:hover{background:#1c2128}
.st{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.s-pending{background:#30363d;color:#8b949e}.s-scanning{background:#1f3a5f;color:#58a6ff}
.s-completed{background:#1a4731;color:#3fb950}.s-failed{background:#5a1e1e;color:#f85149}
.s-discovered{background:#1f3a5f;color:#58a6ff}.s-absorbing{background:#4a3000;color:#d29922}
.s-absorbed{background:#1a4731;color:#3fb950}.s-in_progress{background:#4a3000;color:#d29922}
.cpx{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600}
.cpx-low{background:#1a4731;color:#3fb950}.cpx-medium{background:#4a3000;color:#d29922}.cpx-high{background:#5a1e1e;color:#f85149}
.btn{padding:5px 12px;border:1px solid #30363d;border-radius:6px;cursor:pointer;font-size:12px;background:#21262d;color:#c9d1d9}
.btn:hover{background:#30363d;border-color:#58a6ff}
.btn-p{background:#1f6feb;border-color:#1f6feb;color:#fff}.btn-p:hover{background:#388bfd}
.btn-s{background:#238636;border-color:#238636;color:#fff}.btn-d{background:#da3633;border-color:#da3633;color:#fff}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);z-index:1000}
.mc{background:#161b22;border:1px solid #30363d;margin:6% auto;padding:24px;width:90%;max-width:550px;border-radius:12px}
.mh{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
.mh h2{color:#58a6ff;margin:0;font-size:18px}
.close{color:#8b949e;font-size:26px;cursor:pointer}.close:hover{color:#f85149}
.fg{margin-bottom:14px}.fg label{display:block;margin-bottom:5px;color:#8b949e;font-size:12px}
.fg input,.fg textarea,.fg select{width:100%;padding:9px 12px;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;font-size:13px}
.fg input:focus,.fg select:focus{outline:none;border-color:#58a6ff}
.log{background:#0d1117;border:1px solid #21262d;padding:12px;border-radius:6px;max-height:180px;overflow-y:auto;font-family:monospace;font-size:12px}
.log-entry{margin-bottom:4px}.log-time{color:#484f58}.log-err{color:#f85149}.log-ok{color:#3fb950}.log-info{color:#58a6ff}
.rf{position:fixed;bottom:24px;right:24px;background:#1f6feb;color:#fff;border:none;width:48px;height:48px;border-radius:50%;font-size:18px;cursor:pointer}
.rf:hover{background:#388bfd}.empty{text-align:center;padding:30px;color:#484f58}
.pbar{width:100%;height:6px;background:#21262d;border-radius:3px;overflow:hidden;margin-top:4px}
.pfill{height:100%;background:linear-gradient(90deg,#1f6feb,#58a6ff);border-radius:3px}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;background:#30363d;color:#8b949e}
.search-box{padding:7px 12px;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;font-size:13px;width:220px}
.path{font-family:monospace;font-size:12px}
.cb{accent-color:#58a6ff}
</style></head><body>
<div class="ctn">
<h1>Morphling Absorption System</h1>
<p class="sub">Project Capability Scanning, Analysis & Absorption</p>
<div class="dash">
  <div class="card"><h3>Scans</h3><div class="sg">
    <div><div class="sv" id="s-p">0</div><div class="sl">Pending</div></div>
    <div><div class="sv" id="s-s">0</div><div class="sl">Scanning</div></div>
    <div><div class="sv" id="s-c">0</div><div class="sl">Done</div></div>
    <div><div class="sv" id="s-f">0</div><div class="sl">Failed</div></div>
  </div></div>
  <div class="card"><h3>Capabilities</h3><div class="sg">
    <div><div class="sv" id="c-d">0</div><div class="sl">Found</div></div>
    <div><div class="sv" id="c-a">0</div><div class="sl">Absorbing</div></div>
    <div><div class="sv" id="c-ok">0</div><div class="sl">Absorbed</div></div>
    <div><div class="sv" id="c-f">0</div><div class="sl">Failed</div></div>
  </div></div>
  <div class="card"><h3>Absorption Tasks</h3><div class="sg">
    <div><div class="sv" id="at-p">0</div><div class="sl">Pending</div></div>
    <div><div class="sv" id="at-ip">0</div><div class="sl">Active</div></div>
    <div><div class="sv" id="at-c">0</div><div class="sl">Done</div></div>
    <div><div class="sv" id="at-f">0</div><div class="sl">Failed</div></div>
  </div></div>
</div>
<div class="sec"><h2>Project Scans <div><button class="btn btn-p" onclick="showM('sm')">+ New Scan</button></div></h2>
<table><thead><tr><th>ID</th><th>Project</th><th>Path</th><th>Files</th><th>Modules</th><th>Status</th><th>Actions</th></tr></thead>
<tbody id="sct"></tbody></table></div>
<div class="sec"><h2>Capabilities <div><input class="search-box" id="capS" placeholder="Search..." oninput="filterCaps()">
<button class="btn btn-s" onclick="absorbSelected()">Absorb Selected</button></div></h2>
<table><thead><tr><th><input type="checkbox" class="cb" id="capAll" onchange="toggleAll()"></th>
<th>Name</th><th>Type</th><th>Path</th><th>Description</th><th>Complexity</th><th>Status</th><th>Actions</th></tr></thead>
<tbody id="cpt"></tbody></table></div>
<div class="sec"><h2>Absorption Tasks <div><button class="btn" onclick="load()">Refresh</button></div></h2>
<table><thead><tr><th>ID</th><th>Capability</th><th>Strategy</th><th>Progress</th><th>Status</th><th>Result</th><th>Created</th></tr></thead>
<tbody id="att"></tbody></table></div>
<div class="sec"><h2>System Log</h2><div class="log" id="slog"></div></div>
</div>
<div id="sm" class="modal"><div class="mc"><div class="mh"><h2>Scan Project</h2><span class="close" onclick="hideM('sm')">&times;</span></div>
<form id="sf"><div class="fg"><label>Project Path</label><input id="sp" class="path" placeholder="C:\\path\\to\\project" required></div>
<div class="fg"><label>Project Name (optional)</label><input id="sn"></div>
<button type="submit" class="btn btn-p">Start Scan</button></form></div></div>
<div id="am" class="modal"><div class="mc"><div class="mh"><h2>Absorb Capability</h2><span class="close" onclick="hideM('am')">&times;</span></div>
<form id="af"><div class="fg"><label>Capability</label><input id="ac-name" readonly></div><input type="hidden" id="ac-id">
<div class="fg"><label>Strategy</label><select id="ac-st">
<option value="integrate">Integrate</option><option value="replace">Replace</option>
<option value="extend">Extend</option><option value="reference">Reference</option></select></div>
<button type="submit" class="btn btn-p">Start Absorption</button></form></div></div>
<button class="rf" onclick="load()">&#x21bb;</button>
<script>
let logs=[],allCaps=[],selCaps=new Set();
const esc=s=>s?s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'):'';
const fmt=t=>t?new Date(t*1000).toLocaleString():'-';
const trunc=(s,n=35)=>s&&s.length>n?s.substring(0,n)+'...':s||'-';
function addLog(m,t='info'){logs.unshift({t:new Date().toLocaleTimeString(),m,t});if(logs.length>80)logs.pop();renderLogs();}
function renderLogs(){document.getElementById('slog').innerHTML=logs.map(l=>`<div class="log-entry"><span class="log-time">[${l.t}]</span> <span class="log-${l.t}">${esc(l.m)}</span></div>`).join('');}
function showM(id){document.getElementById(id).style.display='block';}
function hideM(id){document.getElementById(id).style.display='none';}
function filterCaps(){const q=document.getElementById('capS').value.toLowerCase();renderCaps(allCaps.filter(c=>(c.name+c.description+c.cap_type).toLowerCase().includes(q)));}
function toggleAll(){const all=document.getElementById('capAll').checked;document.querySelectorAll('.cap-cb').forEach(cb=>{cb.checked=all;const id=cb.dataset.id;if(all)selCaps.add(id);else selCaps.delete(id);});}
function renderCaps(caps){allCaps=caps;document.getElementById('cpt').innerHTML=caps.length?caps.map(c=>`<tr>
<td><input type="checkbox" class="cb cap-cb" data-id="${c.id}" onchange="togCap('${c.id}',this.checked)" ${selCaps.has(c.id)?'checked':''}></td>
<td><strong>${esc(c.name)}</strong></td><td><span class="tag">${c.cap_type}</span></td>
<td class="path">${esc(trunc(c.module_path))}</td><td>${esc(trunc(c.description,40))}</td>
<td><span class="cpx cpx-${c.complexity}">${c.complexity}</span></td>
<td><span class="st s-${c.status}">${c.status}</span></td>
<td>${c.status==='discovered'||c.status==='failed'?`<button class="btn btn-p" onclick="showAbsorb('${c.id}','${esc(c.name)}')">Absorb</button>`:''}
<button class="btn" onclick="viewCap('${c.id}')">Detail</button></td></tr>`).join(''):'<tr><td colspan="8" class="empty">No capabilities found</td></tr>';}
function togCap(id,checked){if(checked)selCaps.add(id);else selCaps.delete(id);}
function showAbsorb(id,name){document.getElementById('ac-id').value=id;document.getElementById('ac-name').value=name;showM('am');}
async function viewCap(id){const r=await fetch(`/api/capability/${id}`);const c=await r.json();
let html=`<h4>${esc(c.name)}</h4><div><b>Type:</b> ${c.cap_type} | <b>Complexity:</b> ${c.complexity} | <b>Status:</b> ${c.status}</div>
<div style="margin-top:8px"><b>Module:</b> ${esc(c.module_path)}</div><div><b>Description:</b> ${esc(c.description)}</div>`;
try{const fns=JSON.parse(c.functions||'[]');if(fns.length)html+=`<div style="margin-top:8px"><b>Functions (${fns.length}):</b> ${fns.map(esc).join(', ')}</div>`;}catch{}
try{const cls=JSON.parse(c.classes||'[]');if(cls.length)html+=`<div><b>Classes (${cls.length}):</b> ${cls.map(esc).join(', ')}</div>`;}catch{}
try{const deps=JSON.parse(c.dependencies||'[]');if(deps.length)html+=`<div><b>Dependencies:</b> ${deps.slice(0,10).map(esc).join(', ')}${deps.length>10?'...':''}</div>`;}catch{}
document.getElementById('dm-body').innerHTML=html;showM('dm');}
async function absorbSelected(){if(!selCaps.size){addLog('No capabilities selected','err');return;}
const strategy=prompt('Strategy (integrate/replace/extend/reference):','integrate');if(!strategy)return;
for(const id of selCaps){try{await fetch('/api/absorb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({capability_id:id,strategy})});}catch{}}
selCaps.clear();document.getElementById('capAll').checked=false;load();addLog(`Started batch absorption (${strategy})`,'ok');}
function updateUI(d){
const s=d.stats;
document.getElementById('s-p').textContent=s.scans.pending||0;document.getElementById('s-s').textContent=s.scans.scanning||0;
document.getElementById('s-c').textContent=s.scans.completed||0;document.getElementById('s-f').textContent=s.scans.failed||0;
document.getElementById('c-d').textContent=s.capabilities.discovered||0;document.getElementById('c-a').textContent=s.capabilities.absorbing||0;
document.getElementById('c-ok').textContent=s.capabilities.absorbed||0;document.getElementById('c-f').textContent=s.capabilities.failed||0;
document.getElementById('at-p').textContent=s.absorb_tasks.pending||0;document.getElementById('at-ip').textContent=s.absorb_tasks.in_progress||0;
document.getElementById('at-c').textContent=s.absorb_tasks.completed||0;document.getElementById('at-f').textContent=s.absorb_tasks.failed||0;
document.getElementById('sct').innerHTML=d.scans.length?d.scans.map(s=>`<tr>
<td><code>${s.id}</code></td><td><strong>${esc(s.project_name)}</strong></td>
<td class="path">${esc(trunc(s.project_path,28))}</td><td>${s.file_count||0}</td><td>${s.module_count||0}</td>
<td><span class="st s-${s.status}">${s.status}</span></td>
<td>${s.status==='completed'?`<button class="btn" onclick="viewScan('${s.id}')">View</button>`:
s.status==='pending'?`<button class="btn btn-p" onclick="startScan('${s.id}')">Scan</button>`:''}
<button class="btn btn-d" onclick="delScan('${s.id}')">Del</button></td></tr>`).join(''):'<tr><td colspan="7" class="empty">No scans</td></tr>';
renderCaps(d.capabilities);
document.getElementById('att').innerHTML=d.absorb_tasks.length?d.absorb_tasks.map(t=>{
const cn=d.capabilities.find(c=>c.id===t.capability_id);
return `<tr><td><code>${t.id}</code></td><td>${cn?esc(cn.name):t.capability_id}</td>
<td><span class="tag">${t.strategy}</span></td>
<td><div class="pbar"><div class="pfill" style="width:${t.progress||0}%"></div></div><small>${t.progress||0}%</small></td>
<td><span class="st s-${t.status}">${t.status}</span></td>
<td>${esc(trunc(t.result||t.error||'-',40))}</td><td>${fmt(t.created_at)}</td></tr>`}).join(''):'<tr><td colspan="7" class="empty">No absorption tasks</td></tr>';}
async function api(url,method='GET',body=null){const o={method,headers:{'Content-Type':'application/json'}};if(body)o.body=JSON.stringify(body);return(await fetch(url,o)).json();}
async function load(){try{const d=await api('/api/snapshot');updateUI(d);addLog('Refreshed','ok');}catch(e){addLog('Error: '+e.message,'err');}}
async function startScan(id){try{const r=await api(`/api/scan/${id}/start`,'POST');addLog(`Scan ${id}: ${r.status}`,'ok');load();}catch(e){addLog('Scan error: '+e.message,'err');}}
async function delScan(id){if(!confirm('Delete this scan and all related data?'))return;await api(`/api/scan/${id}`,'DELETE');load();addLog('Scan deleted','info');}
async function viewScan(id){const caps=await api(`/api/scan/${id}/capabilities`);const scan=await api(`/api/scan/${id}`);
let html=`<h4>${esc(scan.project_name)}</h4><div><b>Path:</b> ${esc(scan.project_path)}</div>
<div><b>Files:</b> ${scan.file_count} | <b>Modules:</b> ${scan.module_count} | <b>Status:</b> ${scan.status}</div>`;
if(caps.length){html+=`<div style="margin-top:10px"><b>Capabilities (${caps.length}):</b><ul style="margin:6px 0 0 20px">`;
html+=caps.map(c=>`<li><strong>${esc(c.name)}</strong> (${c.cap_type}, ${c.complexity}) - ${esc(c.description)}</li>`).join('');
html+=`</ul></div>`;}
document.getElementById('dm-body').innerHTML=html;showM('dm');}
document.getElementById('sf').onsubmit=async e=>{e.preventDefault();const p=document.getElementById('sp').value;
const n=document.getElementById('sn').value||null;try{await api('/api/scan','POST',{project_path:p,project_name:n});
hideM('sm');e.target.reset();load();addLog('Scan created','ok');}catch(er){addLog('Error: '+er.message,'err');}};
document.getElementById('af').onsubmit=async e=>{e.preventDefault();const id=document.getElementById('ac-id').value;
const st=document.getElementById('ac-st').value;try{await api('/api/absorb','POST',{capability_id:id,strategy:st});
hideM('am');load();addLog(`Absorption started (${st})`,'ok');}catch(er){addLog('Error: '+er.message,'err');}};
let ws=null;function connectWS(){ws=new WebSocket(`ws://${location.host}/ws`);
ws.onopen=()=>addLog('WebSocket connected','ok');
ws.onmessage=e=>{try{const d=JSON.parse(e.data);if(d.type==='update')updateUI(d);}catch{}};
ws.onclose=()=>{addLog('WS disconnected, reconnecting...','info');setTimeout(connectWS,3000);};}
connectWS();load();setInterval(load,20000);
</script></body></html>"""


# ==================== App ====================

app = FastAPI(title="Morphling Absorption System")
ws_clients: Set[WebSocket] = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None


async def broadcast(payload: dict):
    dead = []
    for ws in list(ws_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)


def schedule_broadcast(payload: dict):
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(payload), main_loop)


def push_update(event_type: str = "update"):
    schedule_broadcast({"type": event_type, "timestamp": now(), **snapshot()})


def snapshot():
    with get_db() as db:
        scans = [dict(r) for r in db.execute("SELECT * FROM scans ORDER BY created_at DESC LIMIT 50").fetchall()]
        capabilities = [dict(r) for r in db.execute("SELECT * FROM capabilities ORDER BY created_at DESC LIMIT 200").fetchall()]
        absorb_tasks = [dict(r) for r in db.execute("SELECT * FROM absorb_tasks ORDER BY created_at DESC LIMIT 100").fetchall()]
        stats = {
            "scans": {s: db.execute(f"SELECT COUNT(*) c FROM scans WHERE status='{s}'").fetchone()["c"]
                      for s in ("pending", "scanning", "completed", "failed")},
            "capabilities": {s: db.execute(f"SELECT COUNT(*) c FROM capabilities WHERE status='{s}'").fetchone()["c"]
                             for s in ("discovered", "absorbing", "absorbed", "failed")},
            "absorb_tasks": {s: db.execute(f"SELECT COUNT(*) c FROM absorb_tasks WHERE status='{s}'").fetchone()["c"]
                             for s in ("pending", "in_progress", "completed", "failed")},
        }
    return {"stats": stats, "scans": scans, "capabilities": capabilities, "absorb_tasks": absorb_tasks}


# ==================== WebSocket ====================

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


# ==================== API 路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML)


@app.get("/api/snapshot")
async def api_snapshot():
    return snapshot()


@app.get("/api/stats")
async def api_stats():
    return snapshot()["stats"]


# ---- 扫描 ----

@app.post("/api/scan")
async def api_create_scan(project_path=Body(...), project_name=Body(None)):
    """创建扫描任务"""
    if not os.path.isdir(project_path):
        raise HTTPException(400, f"Invalid directory: {project_path}")
    scan_id = short_id()
    ts = now()
    name = project_name or os.path.basename(os.path.normpath(project_path))
    with get_db() as db:
        db.execute("INSERT INTO scans(id,project_path,project_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   (scan_id, project_path, name, "pending", ts, ts))
    push_update()
    return {"id": scan_id, "status": "pending"}


@app.post("/api/scan/{scan_id}/start")
async def api_start_scan(scan_id: str):
    """执行扫描"""
    with get_db() as db:
        scan = db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if not scan:
            raise HTTPException(404, "Scan not found")
        if scan["status"] != "pending":
            raise HTTPException(400, f"Scan is already {scan['status']}")

    ts = now()
    with get_db() as db:
        db.execute("UPDATE scans SET status='scanning', updated_at=? WHERE id=?", (ts, scan_id))
    push_update()

    try:
        result = CodeAnalyzer.analyze_project(scan["project_path"])
        caps = result["capabilities"]

        with get_db() as db:
            db.execute("UPDATE scans SET status='completed', file_count=?, module_count=?, scan_result=?, updated_at=? WHERE id=?",
                       (result["file_count"], result["module_count"], json.dumps(result), now(), scan_id))
            for cap in caps:
                cap_id = short_id()
                db.execute("""INSERT INTO capabilities(id,scan_id,name,cap_type,module_path,description,
                    functions,classes,dependencies,complexity,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cap_id, scan_id, cap["name"], cap["cap_type"], cap["module_path"], cap["description"],
                     cap["functions"], cap["classes"], cap["dependencies"], cap["complexity"], "discovered", now(), now()))

        push_update()
        return {"id": scan_id, "status": "completed", "file_count": result["file_count"], "module_count": result["module_count"]}

    except Exception as e:
        with get_db() as db:
            db.execute("UPDATE scans SET status='failed', error=?, updated_at=? WHERE id=?", (str(e), now(), scan_id))
        push_update()
        raise HTTPException(500, str(e))


@app.get("/api/scan/{scan_id}")
async def api_get_scan(scan_id: str):
    with get_db() as db:
        scan = db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not scan:
        raise HTTPException(404, "Scan not found")
    return dict(scan)


@app.get("/api/scan/{scan_id}/capabilities")
async def api_get_scan_caps(scan_id: str):
    with get_db() as db:
        rows = db.execute("SELECT * FROM capabilities WHERE scan_id=? ORDER BY complexity DESC, name", (scan_id,)).fetchall()
    return [dict(r) for r in rows]


@app.delete("/api/scan/{scan_id}")
async def api_delete_scan(scan_id: str):
    with get_db() as db:
        db.execute("DELETE FROM absorb_tasks WHERE scan_id=?", (scan_id,))
        db.execute("DELETE FROM capabilities WHERE scan_id=?", (scan_id,))
        result = db.execute("DELETE FROM scans WHERE id=?", (scan_id,))
        if result.rowcount == 0:
            raise HTTPException(404, "Scan not found")
    push_update()
    return {"id": scan_id, "status": "deleted"}


# ---- 能力 ----

@app.get("/api/capabilities")
async def api_list_capabilities(scan_id: str = None, status: str = None, cap_type: str = None, limit: int = 200):
    with get_db() as db:
        q, p = "SELECT * FROM capabilities WHERE 1=1", []
        if scan_id:
            q += " AND scan_id=?"; p.append(scan_id)
        if status:
            q += " AND status=?"; p.append(status)
        if cap_type:
            q += " AND cap_type=?"; p.append(cap_type)
        q += " ORDER BY created_at DESC LIMIT ?"; p.append(limit)
        rows = db.execute(q, p).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/capability/{cap_id}")
async def api_get_capability(cap_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM capabilities WHERE id=?", (cap_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Capability not found")
    return dict(row)


# ---- 吸收 ----

@app.post("/api/absorb")
async def api_start_absorb(capability_id=Body(...), strategy=Body("integrate")):
    """启动吸收流程"""
    valid_strategies = ("integrate", "replace", "extend", "reference")
    if strategy not in valid_strategies:
        raise HTTPException(400, f"Invalid strategy. Must be one of: {valid_strategies}")

    ts = now()
    with get_db() as db:
        cap = db.execute("SELECT * FROM capabilities WHERE id=?", (capability_id,)).fetchone()
        if not cap:
            raise HTTPException(404, "Capability not found")
        if cap["status"] not in ("discovered", "failed"):
            raise HTTPException(400, f"Capability is already {cap['status']}")

        absorb_id = short_id()
        # 通过蜂巢系统发布吸收目标
        hive_goal_id = None
        try:
            import httpx
            resp = httpx.post(f"{HIVE_API_BASE}/api/goal", json={
                "title": f"[Morphling] Absorb: {cap['name']} ({strategy})",
                "description": f"Absorb capability '{cap['name']}' from module '{cap['module_path']}' using strategy: {strategy}",
                "status": "open"
            }, timeout=5)
            if resp.status_code == 200:
                hive_goal_id = resp.json().get("id")
        except Exception:
            pass  # 蜂巢系统不可用时，本地跟踪

        db.execute("""INSERT INTO absorb_tasks(id,scan_id,capability_id,strategy,status,progress,hive_goal_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (absorb_id, cap["scan_id"], capability_id, strategy, "in_progress", 0, hive_goal_id, ts, ts))
        db.execute("UPDATE capabilities SET status='absorbing', absorb_task_id=?, updated_at=? WHERE id=?",
                   (absorb_id, ts, capability_id))

    push_update()

    # 模拟吸收流程（实际场景由 Worker Agent 异步完成）
    asyncio.create_task(_run_absorption(absorb_id, capability_id, strategy, cap))

    return {"id": absorb_id, "capability_id": capability_id, "strategy": strategy, "status": "in_progress"}


async def _run_absorption(absorb_id: str, cap_id: str, strategy: str, cap):
    """模拟/执行吸收流程"""
    try:
        steps = [
            (20, "Analyzing capability structure"),
            (40, "Mapping dependencies"),
            (60, "Generating integration code"),
            (80, "Validating compatibility"),
            (100, "Absorption complete"),
        ]
        for progress, msg in steps:
            await asyncio.sleep(1)  # 模拟处理时间
            with get_db() as db:
                db.execute("UPDATE absorb_tasks SET progress=?, result=?, updated_at=? WHERE id=?",
                           (progress, msg, now(), absorb_id))
            push_update()

        result_msg = f"Successfully absorbed '{cap['name']}' via {strategy}"
        with get_db() as db:
            db.execute("UPDATE absorb_tasks SET status='completed', progress=100, result=?, updated_at=? WHERE id=?",
                       (result_msg, now(), absorb_id))
            db.execute("UPDATE capabilities SET status='absorbed', updated_at=? WHERE id=?", (now(), cap_id))

        # 完成蜂巢任务
        with get_db() as db:
            task = db.execute("SELECT hive_goal_id FROM absorb_tasks WHERE id=?", (absorb_id,)).fetchone()
            if task and task["hive_goal_id"]:
                try:
                    import httpx
                    httpx.put(f"{HIVE_API_BASE}/api/goal/{task['hive_goal_id']}", json={"status": "completed"}, timeout=5)
                except Exception:
                    pass

        push_update()

    except Exception as e:
        with get_db() as db:
            db.execute("UPDATE absorb_tasks SET status='failed', error=?, updated_at=? WHERE id=?", (str(e), now(), absorb_id))
            db.execute("UPDATE capabilities SET status='failed', updated_at=? WHERE id=?", (now(), cap_id))
        push_update()


@app.post("/api/absorb/batch")
async def api_batch_absorb(capability_ids=Body(...), strategy=Body("integrate")):
    """批量吸收"""
    results = []
    for cap_id in capability_ids:
        try:
            r = await api_start_absorb(capability_id=cap_id, strategy=strategy)
            results.append(r)
        except HTTPException as e:
            results.append({"capability_id": cap_id, "error": e.detail})
    return results


@app.get("/api/absorb/{absorb_id}")
async def api_get_absorb(absorb_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM absorb_tasks WHERE id=?", (absorb_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Absorption task not found")
    return dict(row)


# ---- 启动 ----

@app.on_event("startup")
async def on_startup():
    global main_loop
    main_loop = asyncio.get_running_loop()
    init_db()


@app.get("/api/readme")
def api_readme():
    return PlainTextResponse("""Morphling Absorption System API
================================

POST /api/scan                     Create scan (project_path, project_name)
POST /api/scan/{id}/start          Start scanning
GET  /api/scan/{id}                Get scan details
GET  /api/scan/{id}/capabilities   Get scan capabilities
DELETE /api/scan/{id}              Delete scan and related data

GET  /api/capabilities             List capabilities (?scan_id=&status=&cap_type=)
GET  /api/capability/{id}          Get capability details

POST /api/absorb                   Start absorption (capability_id, strategy)
POST /api/absorb/batch             Batch absorption (capability_ids[], strategy)
GET  /api/absorb/{id}              Get absorption status

GET  /api/snapshot                 Full dashboard snapshot
GET  /api/stats                    Stats only
WebSocket /ws                      Real-time updates

Strategies: integrate, replace, extend, reference
""")


if __name__ == "__main__":
    import argparse, uvicorn
    p = argparse.ArgumentParser()
    p.add_argument("--cwd")
    p.add_argument("--port", type=int, default=58802)
    p.add_argument("--db", default="morphling.db")
    a = p.parse_args()
    if a.cwd:
        os.chdir(a.cwd)
    DEFAULT_DB = a.db
    uvicorn.run(app, host="0.0.0.0", port=a.port)
