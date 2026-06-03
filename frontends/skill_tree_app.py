"""Skill Tree Visualizer — FastAPI + WebSocket backend.

Scans memory/ directory recursively, parses SOP file headings,
builds a tree structure, and serves a web UI for visualization.

Usage:
    python frontends/skill_tree_app.py
    # Opens http://127.0.0.1:8901
"""
from __future__ import annotations

import os, sys, re, json, time, asyncio, threading
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

# --- paths ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = os.path.join(ROOT, "memory")
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill_tree.html")

HOST = "127.0.0.1"
PORT = 8901

# --- data model ---
@dataclass
class SkillNode:
    id: str
    name: str
    path: str
    type: str          # 'directory' | 'file'
    children: List["SkillNode"] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "children": [c.to_dict() for c in self.children],
            "metadata": self.metadata,
        }

# --- SOP parsing ---
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SUBTITLE_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_FUNC_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)


def parse_sop_headings(text: str) -> Dict[str, Any]:
    """Extract title, subtitles, and feature points from SOP markdown."""
    title_m = _TITLE_RE.search(text)
    title = title_m.group(1).strip() if title_m else ""
    subtitles = [m.group(1).strip() for m in _SUBTITLE_RE.finditer(text)]
    functions = [m.group(1).strip() for m in _FUNC_RE.finditer(text)]
    return {"title": title, "subtitles": subtitles, "functions": functions}


def _node_id(rel: str) -> str:
    """Stable id from relative path."""
    return rel.replace("\\", "/").replace("/", "__").replace(".", "_")


def scan_memory(root: str = MEMORY_DIR) -> SkillNode:
    """Recursively scan memory/ and build the skill tree."""
    root = os.path.abspath(root)
    root_name = os.path.basename(root)

    def _scan(dir_path: str, rel: str) -> SkillNode:
        node_id = _node_id(rel) if rel else "root"
        node = SkillNode(id=node_id, name=os.path.basename(dir_path) or root_name,
                         path=rel, type="directory")
        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            return node

        # Separate dirs and files
        dirs, files = [], []
        for e in entries:
            full = os.path.join(dir_path, e)
            if os.path.isdir(full):
                dirs.append(e)
            elif e.endswith(".md") or e.endswith(".py"):
                files.append(e)

        for d in dirs:
            child_rel = f"{rel}/{d}" if rel else d
            node.children.append(_scan(os.path.join(dir_path, d), child_rel))

        for f in files:
            child_rel = f"{rel}/{f}" if rel else f
            file_node = SkillNode(
                id=_node_id(child_rel),
                name=f,
                path=child_rel,
                type="file",
            )
            # Parse SOP metadata for .md files
            full_path = os.path.join(dir_path, f)
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read(8192)  # read first 8K
                if f.endswith(".md"):
                    file_node.metadata = parse_sop_headings(content)
                    file_node.metadata["size"] = os.path.getsize(full_path)
                else:
                    file_node.metadata = {"size": os.path.getsize(full_path)}
            except OSError:
                pass
            node.children.append(file_node)

        return node

    return _scan(root, "")


# --- FastAPI app ---
app = FastAPI(title="Skill Tree")

# Cache the tree and notify WS clients on refresh
_tree: Optional[SkillNode] = None
_tree_lock = threading.Lock()
ws_clients: set[WebSocket] = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None


def get_tree(refresh: bool = False) -> SkillNode:
    global _tree
    with _tree_lock:
        if _tree is None or refresh:
            _tree = scan_memory()
        return _tree


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


@app.on_event("startup")
async def on_startup():
    global main_loop
    main_loop = asyncio.get_running_loop()
    get_tree(refresh=True)


@app.get("/")
def index():
    return FileResponse(HTML_PATH)


@app.get("/api/tree")
def api_tree():
    return get_tree().to_dict()


@app.post("/api/refresh")
def api_refresh():
    """Force re-scan memory/ directory."""
    tree = get_tree(refresh=True)
    schedule_broadcast({"type": "tree", "data": tree.to_dict()})
    return {"status": "ok", "nodes": _count_nodes(tree)}


@app.get("/api/file/{path:path}")
def api_read_file(path: str):
    """Read a file from memory/ by relative path."""
    full = os.path.join(MEMORY_DIR, path)
    full = os.path.normpath(full)
    if not full.startswith(os.path.normpath(MEMORY_DIR)):
        return JSONResponse({"error": "path outside memory/"}, status_code=400)
    if not os.path.isfile(full):
        return JSONResponse({"error": "file not found"}, status_code=404)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"path": path, "content": content, "size": len(content)}
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_json({"type": "tree", "data": get_tree().to_dict()})
        while True:
            data = await ws.receive_json()
            if data.get("action") == "refresh":
                tree = get_tree(refresh=True)
                await ws.send_json({"type": "tree", "data": tree.to_dict()})
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)


def _count_nodes(node: SkillNode) -> int:
    return 1 + sum(_count_nodes(c) for c in node.children)


if __name__ == "__main__":
    import uvicorn, webbrowser
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run("skill_tree_app:app", host=HOST, port=PORT, reload=False)
