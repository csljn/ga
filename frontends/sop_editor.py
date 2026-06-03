"""SOP Editor — Online Markdown editor for memory/ SOP files.

Provides file browsing, syntax highlighting, live preview, and save.
Uses FastAPI + WebSocket for real-time sync.

Usage:
    python frontends/sop_editor.py
    # Opens http://127.0.0.1:8902
"""
from __future__ import annotations

import os, sys, re, json, time, asyncio, threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "memory"
HTML_PATH = Path(__file__).resolve().parent / "sop_editor.html"

HOST = "127.0.0.1"
PORT = 8902

app = FastAPI(title="SOP Editor")

# --- Models ---
class SaveReq(BaseModel):
    path: str
    content: str

class CreateReq(BaseModel):
    path: str       # relative to memory/
    content: str = ""

# --- WebSocket state ---
ws_clients: set[WebSocket] = set()
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


# --- Helpers ---
def _safe_path(rel: str) -> Path:
    """Resolve relative path safely under MEMORY_DIR."""
    p = (MEMORY_DIR / rel).resolve()
    mem = MEMORY_DIR.resolve()
    if not str(p).startswith(str(mem)):
        raise ValueError("path outside memory/")
    return p


def _list_files(dir_path: Path, rel: str = "") -> list[dict]:
    """List .md and .py files in directory."""
    items = []
    try:
        entries = sorted(dir_path.iterdir())
    except OSError:
        return items
    for e in entries:
        child_rel = f"{rel}/{e.name}" if rel else e.name
        if e.is_dir():
            items.append({
                "name": e.name, "path": child_rel, "type": "directory",
                "children": _list_files(e, child_rel),
            })
        elif e.suffix in (".md", ".py"):
            items.append({
                "name": e.name, "path": child_rel, "type": "file",
                "size": e.stat().st_size,
                "modified": e.stat().st_mtime,
            })
    return items


# --- Routes ---
@app.on_event("startup")
async def on_startup():
    global main_loop
    main_loop = asyncio.get_running_loop()


@app.get("/")
def index():
    return FileResponse(str(HTML_PATH))


@app.get("/api/files")
def api_files():
    """List all editable files in memory/."""
    return {"files": _list_files(MEMORY_DIR)}


@app.get("/api/file/{path:path}")
def api_read(path: str):
    """Read file content."""
    try:
        p = _safe_path(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        content = p.read_text(encoding="utf-8")
        return {"path": path, "content": content, "size": len(content),
                "modified": p.stat().st_mtime}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/file")
def api_save(req: SaveReq):
    """Save file content."""
    try:
        p = _safe_path(req.path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(req.content, encoding="utf-8")
        # Notify other WS clients
        schedule_broadcast({
            "type": "file_saved",
            "path": req.path,
            "size": len(req.content),
            "modified": p.stat().st_mtime,
        })
        return {"status": "ok", "path": req.path, "size": len(req.content)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/create")
def api_create(req: CreateReq):
    """Create a new file."""
    try:
        p = _safe_path(req.path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if p.exists():
        return JSONResponse({"error": "file already exists"}, status_code=409)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(req.content, encoding="utf-8")
        schedule_broadcast({"type": "file_created", "path": req.path})
        return {"status": "ok", "path": req.path}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/file/{path:path}")
def api_delete(path: str):
    """Delete a file."""
    try:
        p = _safe_path(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        p.unlink()
        schedule_broadcast({"type": "file_deleted", "path": path})
        return {"status": "ok", "path": path}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_json({"type": "hello", "files": _list_files(MEMORY_DIR)})
        while True:
            data = await ws.receive_json()
            # Client can request file list refresh
            if data.get("action") == "list":
                await ws.send_json({"type": "files", "files": _list_files(MEMORY_DIR)})
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)


if __name__ == "__main__":
    import uvicorn, webbrowser
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run("sop_editor:app", host=HOST, port=PORT, reload=False)
