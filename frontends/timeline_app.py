"""对话回溯系统 (Timeline)
- 解析 temp/model_responses/ 日志构建对话时间线
- 支持时间线导航 (前进/后退)
- 支持从历史节点 Fork 分支
- 多时间线管理 (列表/删除/重命名)
- FastAPI + WebSocket 实时更新
"""
import ast, glob, json, os, re, sys, time, uuid, asyncio, threading, queue
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path

# allow: python frontends/timeline_app.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ---------- config ----------
HOST = "127.0.0.1"
PORT = 8910
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timeline.html")
LOG_DIR = os.path.join(ROOT, "temp", "model_responses")
TIMELINE_STORE = os.path.join(ROOT, "temp", "timelines")
LOG_GLOB = os.path.join(LOG_DIR, "model_responses_*.txt")
BLOCK_RE = re.compile(
    r"^=== (Prompt|Response) ===.*?\n(.*?)(?=^=== (?:Prompt|Response) ===|\Z)",
    re.DOTALL | re.MULTILINE,
)
SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)

# ---------- data models ----------
@dataclass
class MessageNode:
    """时间线中的一个消息节点"""
    id: str
    role: str           # "user" | "assistant" | "system"
    content: str
    timestamp: float    # epoch seconds
    turn: int = 0
    tool_info: str = ""  # 工具调用摘要

@dataclass
class Timeline:
    """一条对话时间线"""
    id: str
    name: str
    parent_id: Optional[str] = None      # 分支来源时间线 id
    fork_point: Optional[str] = None     # 分支点消息 node id
    source_file: Optional[str] = None    # 来源日志文件
    messages: List[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cursor: int = -1  # 当前导航位置 (-1 = 尾部)

# ---------- persistence ----------
def _ensure_dir():
    os.makedirs(TIMELINE_STORE, exist_ok=True)

def _save_timeline(tl: Timeline):
    _ensure_dir()
    path = os.path.join(TIMELINE_STORE, f"{tl.id}.json")
    data = asdict(tl)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_timeline(tid: str) -> Optional[Timeline]:
    path = os.path.join(TIMELINE_STORE, f"{tid}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return Timeline(**data)

def _list_saved() -> List[Timeline]:
    _ensure_dir()
    tls = []
    for p in glob.glob(os.path.join(TIMELINE_STORE, "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            tls.append(Timeline(**data))
        except Exception:
            continue
    tls.sort(key=lambda t: t.updated_at, reverse=True)
    return tls

def _delete_timeline_file(tid: str) -> bool:
    path = os.path.join(TIMELINE_STORE, f"{tid}.json")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False

# ---------- log parsing ----------
def _short_id() -> str:
    return uuid.uuid4().hex[:10]

def _pairs(content: str):
    """Extract (prompt_body, response_body) pairs from log content."""
    blocks = BLOCK_RE.findall(content or "")
    pairs, pending = [], None
    for label, body in blocks:
        if label == "Prompt":
            pending = body.strip()
        elif pending is not None:
            pairs.append((pending, body.strip()))
            pending = None
    return pairs

def _user_text(prompt_body: str) -> str:
    """Extract user text from a prompt JSON body."""
    try:
        msg = json.loads(prompt_body)
    except Exception:
        # fallback: raw text
        for line in prompt_body.splitlines():
            s = line.strip()
            if s and not s.startswith('###') and not s.startswith('{'):
                return s[:200]
        return ""
    if not isinstance(msg, dict):
        return ""
    blocks = msg.get("content", []) or []
    # skip tool_result-only prompts (auto-continuation)
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
        return ""
    parts = []
    for blk in blocks:
        if isinstance(blk, dict) and blk.get("type") == "text":
            t = (blk.get("text") or "").strip()
            if t and not t.startswith("### [WORKING MEMORY]") and "<history>" not in t:
                parts.append(t)
    return "\n".join(parts)[:500]

def _assistant_text(response_body: str) -> str:
    """Extract assistant text from response blocks repr."""
    try:
        blocks = ast.literal_eval(response_body)
    except Exception:
        return response_body[:300] if response_body else ""
    if not isinstance(blocks, list):
        return str(blocks)[:300]
    texts = []
    for b in blocks:
        if isinstance(b, dict):
            if b.get("type") == "text":
                s = b.get("text", "")
                if isinstance(s, str) and s.strip():
                    texts.append(s)
    return "\n".join(texts)[:1000]

def _extract_summary(response_body: str) -> str:
    text = _assistant_text(response_body)
    m = SUMMARY_RE.search(text)
    return m.group(1).strip()[:200] if m else ""

def _extract_tools(response_body: str) -> str:
    """Extract tool-use summary from response."""
    try:
        blocks = ast.literal_eval(response_body)
    except Exception:
        return ""
    if not isinstance(blocks, list):
        return ""
    tools = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            name = b.get("name", "?")
            tools.append(name)
    return ", ".join(tools) if tools else ""

def _timestamp_from_header(content: str) -> float:
    """Extract timestamp from '=== Prompt === 2026-05-28 10:56:46' header."""
    m = re.search(r"=== (?:Prompt|Response) ===\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", content)
    if m:
        try:
            from datetime import datetime
            return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            pass
    return time.time()

def parse_log_file(filepath: str) -> List[MessageNode]:
    """Parse a model_responses log file into MessageNode list."""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return []
    pairs = _pairs(content)
    if not pairs:
        return []
    nodes = []
    turn = 0
    for i, (prompt, response) in enumerate(pairs):
        ts = time.time()
        # Try to extract timestamp from first header
        if i == 0:
            ts = _timestamp_from_header(content)
        user_txt = _user_text(prompt)
        if user_txt:
            turn += 1
            nodes.append(MessageNode(
                id=_short_id(),
                role="user",
                content=user_txt,
                timestamp=ts + turn * 2,
                turn=turn,
            ))
        asst_txt = _assistant_text(response)
        summary = _extract_summary(response)
        tools = _extract_tools(response)
        display = summary if summary else asst_txt
        if display:
            nodes.append(MessageNode(
                id=_short_id(),
                role="assistant",
                content=display,
                timestamp=ts + turn * 2 + 1,
                turn=turn,
                tool_info=tools,
            ))
    return nodes

def import_log_as_timeline(filepath: str) -> Optional[Timeline]:
    """Import a log file as a new timeline."""
    nodes = parse_log_file(filepath)
    if not nodes:
        return None
    fname = os.path.basename(filepath)
    tl = Timeline(
        id=_short_id(),
        name=fname.replace(".txt", ""),
        source_file=filepath,
        messages=[asdict(n) for n in nodes],
        created_at=time.time(),
        updated_at=time.time(),
        cursor=len(nodes) - 1,
    )
    _save_timeline(tl)
    return tl

# ---------- WebSocket hub ----------
ws_clients: set = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None

def schedule_broadcast(payload: dict):
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(payload), main_loop)

async def broadcast(payload: dict):
    dead = []
    for ws in list(ws_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)

# ---------- FastAPI app ----------
app = FastAPI(title="Timeline")

class ImportIn(BaseModel):
    file: Optional[str] = None  # specific file path; None = import all

class ForkIn(BaseModel):
    message_id: str  # fork point message id

class RenameIn(BaseModel):
    name: str

class NavigateIn(BaseModel):
    direction: str  # "prev" | "next" | "first" | "last" | number

class SendIn(BaseModel):
    content: str  # user message to send

@app.on_event("startup")
async def on_startup():
    global main_loop
    main_loop = asyncio.get_running_loop()

@app.get("/")
def index():
    return FileResponse(HTML_PATH)

@app.get("/api/timelines")
def list_timelines():
    """List all timelines."""
    tls = _list_saved()
    return {"items": [_timeline_summary(t) for t in tls]}

def _timeline_summary(tl: Timeline) -> dict:
    msgs = tl.messages
    # find the message at cursor
    cursor_msg = None
    if 0 <= tl.cursor < len(msgs):
        m = msgs[tl.cursor]
        cursor_msg = {"id": m["id"], "role": m["role"], "content": m["content"][:100], "turn": m.get("turn", 0)}
    return {
        "id": tl.id,
        "name": tl.name,
        "parent_id": tl.parent_id,
        "fork_point": tl.fork_point,
        "source_file": tl.source_file,
        "message_count": len(msgs),
        "cursor": tl.cursor,
        "cursor_msg": cursor_msg,
        "created_at": tl.created_at,
        "updated_at": tl.updated_at,
    }

@app.get("/api/timelines/{tid}")
def get_timeline(tid: str, offset: int = 0, limit: int = 50):
    """Get timeline detail with messages window."""
    tl = _load_timeline(tid)
    if not tl:
        return JSONResponse({"error": "timeline not found"}, status_code=404)
    msgs = tl.messages
    total = len(msgs)
    # Return messages around cursor or from offset
    if offset == 0 and limit == 50:
        # Default: show around cursor
        center = tl.cursor if tl.cursor >= 0 else total - 1
        start = max(0, center - limit // 2)
        end = min(total, start + limit)
    else:
        start = offset
        end = min(total, offset + limit)
    window = msgs[start:end]
    return {
        "id": tl.id,
        "name": tl.name,
        "parent_id": tl.parent_id,
        "fork_point": tl.fork_point,
        "source_file": tl.source_file,
        "messages": window,
        "total": total,
        "cursor": tl.cursor,
        "window_start": start,
        "window_end": end,
        "created_at": tl.created_at,
        "updated_at": tl.updated_at,
    }

@app.post("/api/timelines/import")
def import_timelines(body: ImportIn):
    """Import log files as timelines."""
    imported = []
    if body.file:
        tl = import_log_as_timeline(body.file)
        if tl:
            imported.append(_timeline_summary(tl))
    else:
        # Import all log files
        files = sorted(glob.glob(LOG_GLOB), key=os.path.getmtime, reverse=True)
        for f in files[:20]:  # limit to 20 most recent
            # Check if already imported
            existing = _list_saved()
            if any(t.source_file == f for t in existing):
                continue
            tl = import_log_as_timeline(f)
            if tl:
                imported.append(_timeline_summary(tl))
    schedule_broadcast({"type": "timelines_updated"})
    return {"imported": imported, "count": len(imported)}

@app.post("/api/timelines/{tid}/fork")
def fork_timeline(tid: str, body: ForkIn):
    """Fork a timeline at a specific message point."""
    tl = _load_timeline(tid)
    if not tl:
        return JSONResponse({"error": "timeline not found"}, status_code=404)
    # Find the message
    fork_idx = None
    for i, m in enumerate(tl.messages):
        if m["id"] == body.message_id:
            fork_idx = i
            break
    if fork_idx is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    # Create forked timeline with messages up to fork point
    forked = Timeline(
        id=_short_id(),
        name=f"{tl.name} [fork@{fork_idx}]",
        parent_id=tid,
        fork_point=body.message_id,
        source_file=tl.source_file,
        messages=tl.messages[:fork_idx + 1],
        created_at=time.time(),
        updated_at=time.time(),
        cursor=fork_idx,
    )
    _save_timeline(forked)
    schedule_broadcast({"type": "timelines_updated"})
    return _timeline_summary(forked)

@app.post("/api/timelines/{tid}/send")
def send_message_to_timeline(tid: str, body: SendIn):
    """Send a user message to a timeline (append at cursor position)."""
    tl = _load_timeline(tid)
    if not tl:
        return JSONResponse({"error": "timeline not found"}, status_code=404)
    # Add user message after cursor
    insert_idx = tl.cursor + 1 if tl.cursor >= 0 else len(tl.messages)
    user_node = MessageNode(
        id=_short_id(),
        role="user",
        content=body.content,
        timestamp=time.time(),
        turn=(tl.messages[tl.cursor].get("turn", 0) + 1) if 0 <= tl.cursor < len(tl.messages) else 1,
    )
    tl.messages.insert(insert_idx, asdict(user_node))
    tl.cursor = insert_idx
    tl.updated_at = time.time()
    _save_timeline(tl)
    schedule_broadcast({"type": "timeline_updated", "id": tid})
    return {"ok": True, "message_id": user_node.id, "cursor": tl.cursor}

@app.delete("/api/timelines/{tid}")
def delete_timeline(tid: str):
    """Delete a timeline."""
    if _delete_timeline_file(tid):
        schedule_broadcast({"type": "timelines_updated"})
        return {"ok": True}
    return JSONResponse({"error": "timeline not found"}, status_code=404)

@app.post("/api/timelines/{tid}/rename")
def rename_timeline(tid: str, body: RenameIn):
    """Rename a timeline."""
    tl = _load_timeline(tid)
    if not tl:
        return JSONResponse({"error": "timeline not found"}, status_code=404)
    tl.name = body.name
    tl.updated_at = time.time()
    _save_timeline(tl)
    schedule_broadcast({"type": "timelines_updated"})
    return {"ok": True, "name": tl.name}

@app.post("/api/timelines/{tid}/navigate")
def navigate_timeline(tid: str, body: NavigateIn):
    """Navigate the cursor in a timeline."""
    tl = _load_timeline(tid)
    if not tl:
        return JSONResponse({"error": "timeline not found"}, status_code=404)
    total = len(tl.messages)
    if total == 0:
        return {"ok": True, "cursor": -1}
    direction = body.direction
    if direction == "prev":
        tl.cursor = max(0, tl.cursor - 1)
    elif direction == "next":
        tl.cursor = min(total - 1, tl.cursor + 1)
    elif direction == "first":
        tl.cursor = 0
    elif direction == "last":
        tl.cursor = total - 1
    else:
        try:
            idx = int(direction)
            tl.cursor = max(0, min(total - 1, idx))
        except ValueError:
            return JSONResponse({"error": "invalid direction"}, status_code=400)
    tl.updated_at = time.time()
    _save_timeline(tl)
    schedule_broadcast({"type": "timeline_updated", "id": tid})
    return {"ok": True, "cursor": tl.cursor, "message": tl.messages[tl.cursor] if 0 <= tl.cursor < total else None}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        tls = _list_saved()
        await ws.send_json({"type": "hello", "timelines": [_timeline_summary(t) for t in tls]})
        while True:
            data = await ws.receive_json()
            # Client can send commands via WS
            action = data.get("action")
            if action == "list":
                tls = _list_saved()
                await ws.send_json({"type": "timelines", "items": [_timeline_summary(t) for t in tls]})
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)

if __name__ == "__main__":
    import uvicorn, webbrowser
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run("timeline_app:app", host=HOST, port=PORT, reload=False)
