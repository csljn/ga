# agent_bbs.py — Hive BBS核心逻辑
# 蜂巢系统消息板：支持目标发布、Worker注册、任务领取、结果验收

import sqlite3
import uuid
import time
import json
import os
from threading import Lock
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from contextlib import contextmanager

# 默认数据库路径
DEFAULT_DB = "hive_bbs.db"

app = FastAPI(title="Hive BBS", docs_url=None, redoc_url=None, openapi_url=None)

# 数据库锁
db_lock = Lock()

@contextmanager
def get_db(db_path=None):
    """获取数据库连接的上下文管理器"""
    path = db_path or DEFAULT_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db(db_path=None):
    """初始化数据库表结构"""
    with get_db(db_path) as db:
        # 目标表
        db.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'open',
                created_at REAL,
                updated_at REAL
            )
        """)
        
        # Worker表
        db.execute("""
            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                capabilities TEXT,
                status TEXT DEFAULT 'idle',
                last_seen REAL,
                created_at REAL
            )
        """)
        
        # 任务表
        db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                worker_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'pending',
                result TEXT,
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY(goal_id) REFERENCES goals(id),
                FOREIGN KEY(worker_id) REFERENCES workers(id)
            )
        """)
        
        # 帖子表（兼容原BBS功能）
        db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                post_type TEXT DEFAULT 'message',
                ref_id TEXT,
                created_at REAL
            )
        """)
        
        # 创建索引
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_worker ON tasks(worker_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at)")

def now():
    """获取当前时间戳"""
    return time.time()

def short_id():
    """生成短ID"""
    return uuid.uuid4().hex[:8]

# ==================== 目标相关API ====================

@app.post("/goal")
def create_goal(title=Body(...), description=Body(""), status=Body("open")):
    """创建新目标"""
    goal_id = short_id()
    ts = now()
    with get_db() as db:
        db.execute(
            "INSERT INTO goals(id, title, description, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (goal_id, title, description, status, ts, ts)
        )
    return {"id": goal_id, "title": title, "status": status}

@app.get("/goals")
def list_goals(status=Query(None), limit=Query(50), offset=Query(0)):
    """获取目标列表"""
    with get_db() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM goals WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM goals ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
    return [dict(r) for r in rows]

@app.get("/goal/{goal_id}")
def get_goal(goal_id: str):
    """获取单个目标详情"""
    with get_db() as db:
        row = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Goal not found")
    return dict(row)

@app.put("/goal/{goal_id}")
def update_goal(goal_id: str, title=Body(None), description=Body(None), status=Body(None)):
    """更新目标信息"""
    with get_db() as db:
        goal = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        if not goal:
            raise HTTPException(404, "Goal not found")
        
        updates = []
        params = []
        if title is not None:
            updates.append("title=?")
            params.append(title)
        if description is not None:
            updates.append("description=?")
            params.append(description)
        if status is not None:
            updates.append("status=?")
            params.append(status)
        
        if updates:
            updates.append("updated_at=?")
            params.append(now())
            params.append(goal_id)
            db.execute(f"UPDATE goals SET {','.join(updates)} WHERE id=?", params)
    
    return {"id": goal_id, "status": "updated"}

# ==================== Worker相关API ====================

@app.post("/worker/register")
def register_worker(name=Body(...), capabilities=Body([])):
    """注册Worker"""
    worker_id = short_id()
    ts = now()
    caps_json = json.dumps(capabilities) if isinstance(capabilities, list) else capabilities
    with get_db() as db:
        db.execute(
            "INSERT INTO workers(id, name, capabilities, status, last_seen, created_at) VALUES(?,?,?,?,?,?)",
            (worker_id, name, caps_json, "idle", ts, ts)
        )
    return {"id": worker_id, "name": name, "status": "idle"}

@app.post("/worker/{worker_id}/heartbeat")
def worker_heartbeat(worker_id: str):
    """Worker心跳更新"""
    with get_db() as db:
        result = db.execute("UPDATE workers SET last_seen=? WHERE id=?", (now(), worker_id))
        if result.rowcount == 0:
            raise HTTPException(404, "Worker not found")
    return {"id": worker_id, "last_seen": now()}

@app.post("/worker/{worker_id}/status")
def update_worker_status(worker_id: str, status=Body(...)):
    """更新Worker状态"""
    if status not in ("idle", "busy", "offline"):
        raise HTTPException(400, "Invalid status. Must be: idle, busy, offline")
    with get_db() as db:
        result = db.execute(
            "UPDATE workers SET status=?, last_seen=? WHERE id=?",
            (status, now(), worker_id)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Worker not found")
    return {"id": worker_id, "status": status}

@app.get("/workers")
def list_workers(status=Query(None)):
    """获取Worker列表"""
    with get_db() as db:
        if status:
            rows = db.execute("SELECT * FROM workers WHERE status=?", (status,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM workers ORDER BY last_seen DESC").fetchall()
    return [dict(r) for r in rows]

@app.get("/worker/{worker_id}")
def get_worker(worker_id: str):
    """获取单个Worker详情"""
    with get_db() as db:
        row = db.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Worker not found")
    return dict(row)

# ==================== 任务相关API ====================

@app.post("/task")
def create_task(goal_id=Body(...), title=Body(...), description=Body(""), worker_id=Body(None)):
    """创建新任务"""
    task_id = short_id()
    ts = now()
    status = "pending"
    with get_db() as db:
        # 验证goal存在
        goal = db.execute("SELECT id FROM goals WHERE id=?", (goal_id,)).fetchone()
        if not goal:
            raise HTTPException(404, "Goal not found")
        
        # 如果指定了worker，验证worker存在并更新状态
        if worker_id:
            worker = db.execute("SELECT id FROM workers WHERE id=?", (worker_id,)).fetchone()
            if not worker:
                raise HTTPException(404, "Worker not found")
            status = "in_progress"
            db.execute("UPDATE workers SET status='busy', last_seen=? WHERE id=?", (ts, worker_id))
        
        db.execute(
            "INSERT INTO tasks(id, goal_id, worker_id, title, description, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (task_id, goal_id, worker_id, title, description, status, ts, ts)
        )
    return {"id": task_id, "goal_id": goal_id, "status": status}

@app.post("/task/{task_id}/claim")
def claim_task(task_id: str, worker_id=Body(...)):
    """Worker领取任务"""
    ts = now()
    with get_db() as db:
        # 验证任务存在且状态为pending
        task = db.execute("SELECT * FROM tasks WHERE id=? AND status='pending'", (task_id,)).fetchone()
        if not task:
            raise HTTPException(400, "Task not available for claiming")
        
        # 验证worker存在
        worker = db.execute("SELECT id FROM workers WHERE id=?", (worker_id,)).fetchone()
        if not worker:
            raise HTTPException(404, "Worker not found")
        
        # 更新任务状态
        db.execute(
            "UPDATE tasks SET worker_id=?, status='in_progress', updated_at=? WHERE id=?",
            (worker_id, ts, task_id)
        )
        
        # 更新worker状态
        db.execute("UPDATE workers SET status='busy', last_seen=? WHERE id=?", (ts, worker_id))
    
    return {"id": task_id, "worker_id": worker_id, "status": "in_progress"}

@app.post("/task/{task_id}/complete")
def complete_task(task_id: str, result=Body(...), status=Body("completed")):
    """完成任务并提交结果"""
    if status not in ("completed", "failed"):
        raise HTTPException(400, "Invalid status. Must be: completed, failed")
    
    ts = now()
    with get_db() as db:
        task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "Task not found")
        
        # 更新任务状态和结果
        db.execute(
            "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
            (status, result, ts, task_id)
        )
        
        # 如果有worker，释放worker
        if task["worker_id"]:
            db.execute(
                "UPDATE workers SET status='idle', last_seen=? WHERE id=?",
                (ts, task["worker_id"])
            )
    
    return {"id": task_id, "status": status}

@app.post("/task/{task_id}/fail")
def fail_task(task_id: str, reason=Body(...)):
    """标记任务失败"""
    return complete_task(task_id, result=reason, status="failed")

@app.get("/tasks")
def list_tasks(goal_id=Query(None), worker_id=Query(None), status=Query(None), limit=Query(50)):
    """获取任务列表"""
    with get_db() as db:
        query = "SELECT * FROM tasks WHERE 1=1"
        params = []
        
        if goal_id:
            query += " AND goal_id=?"
            params.append(goal_id)
        if worker_id:
            query += " AND worker_id=?"
            params.append(worker_id)
        if status:
            query += " AND status=?"
            params.append(status)
        
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        
        rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]

@app.get("/task/{task_id}")
def get_task(task_id: str):
    """获取单个任务详情"""
    with get_db() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    return dict(row)

# ==================== 帖子相关API（兼容原BBS） ====================

@app.post("/post")
def create_post(author=Body(...), content=Body(...), post_type=Body("message"), ref_id=Body(None)):
    """发布帖子"""
    ts = now()
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO posts(author, content, post_type, ref_id, created_at) VALUES(?,?,?,?,?)",
            (author, content, post_type, ref_id, ts)
        )
        post_id = cur.lastrowid
    return {"id": post_id, "author": author}

@app.get("/posts")
def list_posts(author=Query(None), post_type=Query(None), limit=Query(50), offset=Query(0)):
    """获取帖子列表"""
    with get_db() as db:
        query = "SELECT * FROM posts WHERE 1=1"
        params = []
        
        if author:
            query += " AND author=?"
            params.append(author)
        if post_type:
            query += " AND post_type=?"
            params.append(post_type)
        
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]

@app.get("/poll")
def poll_posts(since_id=Query(0), limit=Query(50)):
    """轮询新帖子"""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM posts WHERE id>? ORDER BY id LIMIT ?",
            (since_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]

# ==================== 统计API ====================

@app.get("/stats")
def get_stats():
    """获取系统统计信息"""
    with get_db() as db:
        goals_open = db.execute("SELECT COUNT(*) c FROM goals WHERE status='open'").fetchone()["c"]
        goals_in_progress = db.execute("SELECT COUNT(*) c FROM goals WHERE status='in_progress'").fetchone()["c"]
        goals_completed = db.execute("SELECT COUNT(*) c FROM goals WHERE status='completed'").fetchone()["c"]
        
        workers_idle = db.execute("SELECT COUNT(*) c FROM workers WHERE status='idle'").fetchone()["c"]
        workers_busy = db.execute("SELECT COUNT(*) c FROM workers WHERE status='busy'").fetchone()["c"]
        workers_offline = db.execute("SELECT COUNT(*) c FROM workers WHERE status='offline'").fetchone()["c"]
        
        tasks_pending = db.execute("SELECT COUNT(*) c FROM tasks WHERE status='pending'").fetchone()["c"]
        tasks_in_progress = db.execute("SELECT COUNT(*) c FROM tasks WHERE status='in_progress'").fetchone()["c"]
        tasks_completed = db.execute("SELECT COUNT(*) c FROM tasks WHERE status='completed'").fetchone()["c"]
        tasks_failed = db.execute("SELECT COUNT(*) c FROM tasks WHERE status='failed'").fetchone()["c"]
    
    return {
        "goals": {"open": goals_open, "in_progress": goals_in_progress, "completed": goals_completed},
        "workers": {"idle": workers_idle, "busy": workers_busy, "offline": workers_offline},
        "tasks": {"pending": tasks_pending, "in_progress": tasks_in_progress, "completed": tasks_completed, "failed": tasks_failed}
    }

@app.get("/readme")
def readme():
    """API使用说明"""
    text = """Hive BBS API
============

目标管理:
  POST /goal           创建目标 (body: title, description, status)
  GET  /goals          获取目标列表 (?status=open/in_progress/completed)
  GET  /goal/{id}      获取目标详情
  PUT  /goal/{id}      更新目标

Worker管理:
  POST /worker/register     注册Worker (body: name, capabilities)
  POST /worker/{id}/heartbeat  Worker心跳
  POST /worker/{id}/status  更新Worker状态 (body: status=idle/busy/offline)
  GET  /workers             获取Worker列表 (?status=idle/busy/offline)

任务管理:
  POST /task           创建任务 (body: goal_id, title, description, worker_id)
  POST /task/{id}/claim    领取任务 (body: worker_id)
  POST /task/{id}/complete 完成任务 (body: result, status=completed/failed)
  POST /task/{id}/fail     标记任务失败 (body: reason)
  GET  /tasks              获取任务列表 (?goal_id=&worker_id=&status=)
  GET  /task/{id}          获取任务详情

帖子（兼容BBS）:
  POST /post           发帖 (body: author, content, post_type, ref_id)
  GET  /posts          帖子列表 (?author=&post_type=&limit=&offset=)
  GET  /poll           轮询新帖 (?since_id=&limit=)

统计:
  GET  /stats          系统统计信息
"""
    return PlainTextResponse(text)

@app.on_event("startup")
def startup():
    """应用启动时初始化数据库"""
    init_db()

if __name__ == "__main__":
    import argparse
    import uvicorn
    
    parser = argparse.ArgumentParser(description="Hive BBS Server")
    parser.add_argument("--cwd", help="工作目录")
    parser.add_argument("--port", type=int, default=58800, help="服务端口")
    parser.add_argument("--db", default="hive_bbs.db", help="数据库文件路径")
    args = parser.parse_args()
    
    if args.cwd:
        os.chdir(args.cwd)
    if args.db:
        DEFAULT_DB = args.db
    
    uvicorn.run(app, host="0.0.0.0", port=args.port)
