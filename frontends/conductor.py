import os, sys, re, time, json, uuid, queue, asyncio, threading, builtins
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Set

# Silence print() from subagent threads (they share stdout with conductor)
_original_print = builtins.print
def _filtered_print(*args, **kwargs):
    t = threading.current_thread()
    if t.name.startswith('subagent-'):
        return
    return _original_print(*args, **kwargs)
builtins.print = _filtered_print

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse
from pydantic import BaseModel

# allow: python frontends/conductor.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agentmain import GenericAgent

HOST = "127.0.0.1"
PORT = 8900
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conductor.html")

app = FastAPI(title="Conductor")

class ChatIn(BaseModel):
    msg: str
    role: str = "conductor"  # conductor | system | user

class StartSubagentIn(BaseModel):
    prompt: str
    depends_on: Optional[List[str]] = None  # DAG dependency task IDs

class SubagentActionIn(BaseModel):
    action: str = "intervene"  # intervene | abort | kill
    msg: str = ""

@dataclass
class SubAgentState:
    id: str
    agent: GenericAgent
    prompt: str
    reply: str = ""
    status: str = "running"  # running | stopped | failed | aborted | pending
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_done: str = ""
    monitor_threads: List[threading.Thread] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)  # DAG dependency task IDs

subagents: Dict[str, SubAgentState] = {}
sub_lock = threading.RLock()
ws_clients: set[WebSocket] = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None
# conductor event queue: only user messages and subagent-done events enter here.
conductor_events: "queue.Queue[dict]" = queue.Queue()
conductor_agent: Optional[GenericAgent] = None
conductor_started = False
chat_messages: List[dict] = []

# ─── DAG Scheduling ──────────────────────────────────────────────────────────
# Pending tasks waiting for dependencies to complete
pending_tasks: Dict[str, dict] = {}  # task_id -> {"prompt": str, "depends_on": List[str]}


def can_start_task(task_id: str) -> bool:
    """检查任务是否可以启动（所有依赖都已完成）"""
    with sub_lock:
        task = subagents.get(task_id)
        if not task:
            task_info = pending_tasks.get(task_id)
            if not task_info:
                return False
            deps = task_info.get("depends_on", [])
        else:
            deps = task.depends_on
        if not deps:
            return True
        for dep in deps:
            dep_task = subagents.get(dep)
            if not dep_task or dep_task.status != "stopped":
                return False
        return True


def check_pending_tasks():
    """检查等待队列，启动所有依赖已满足的任务"""
    to_start = []
    with sub_lock:
        for task_id in list(pending_tasks.keys()):
            if can_start_task(task_id):
                info = pending_tasks.pop(task_id)
                to_start.append((task_id, info))
    for task_id, info in to_start:
        add_chat(f"[DAG] 依赖已满足，启动子任务: {info['prompt'][:60]}...", role="system")
        start_subagent(info["prompt"], depends_on=info["depends_on"], task_id=task_id)


def has_cycle(depends_on_map: Dict[str, List[str]]) -> bool:
    """检查依赖关系是否存在循环"""
    visited: Set[str] = set()
    rec_stack: Set[str] = set()

    def _dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for dep in depends_on_map.get(node, []):
            if dep not in visited:
                if _dfs(dep):
                    return True
            elif dep in rec_stack:
                return True
        rec_stack.discard(node)
        return False

    for node in depends_on_map:
        if node not in visited:
            if _dfs(node):
                return True
    return False

def now_ms() -> int:
    return int(time.time() * 1000)

def short_id() -> str:
    return uuid.uuid4().hex[:8]

_TURN_SPLIT_RE = re.compile(r'\**LLM Running \(Turn \d+\) \.\.\.\**')
_SUMMARY_RE = re.compile(r'<summary>(.*?)</summary>\s*', re.DOTALL)

def extract_last_summary(full: str) -> str:
    """Extract the latest <summary> content for in-progress display."""
    matches = _SUMMARY_RE.findall(full or "")
    if not matches:
        return ""
    s = matches[-1].strip()
    return s[-1000:] if len(s) > 1000 else s

def extract_last_text_reply(full: str) -> str:
    """Extract only the last turn's text reply (like stapp.py fold_turns logic)."""
    # Split by turn markers, take last segment
    parts = _TURN_SPLIT_RE.split(full)
    last = parts[-1] if parts else full
    # Strip <summary> tags
    last = _SUMMARY_RE.sub('', last)
    # Strip [Status] and [Info] lines
    last = re.sub(r'\[(Status|Info)\][^\n]*\n?', '', last)
    # Strip trailing whitespace
    last = last.strip()
    # Cap length
    return last[-3000:] if len(last) > 3000 else last

def subagent_snapshot() -> list[dict]:
    with sub_lock:
        items = []
        for s in subagents.values():
            if s.status == "aborted":
                continue
            items.append({
                "id": s.id,
                "prompt": s.prompt,
                "reply": (extract_last_summary(s.reply) if s.status == "running" else extract_last_text_reply(s.reply)) if s.reply else "",
                "status": s.status,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "depends_on": s.depends_on,
            })
        # Also include pending tasks (not yet started)
        for tid, info in pending_tasks.items():
            items.append({
                "id": tid,
                "prompt": info["prompt"],
                "reply": "",
                "status": "pending",
                "created_at": 0,
                "updated_at": 0,
                "depends_on": info.get("depends_on", []),
            })
        return items

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

def push_cards():
    schedule_broadcast({"type": "subagents", "items": subagent_snapshot()})

def add_chat(msg: str, role: str = "conductor"):
    item = {"id": short_id(), "role": role, "msg": msg, "ts": now_ms(), "read": role != "user"}
    chat_messages.append(item)
    if len(chat_messages) > 200:
        del chat_messages[:-200]
    schedule_broadcast({"type": "chat", "item": item})
    return item

def start_agent_runner(agent: GenericAgent, name: str):
    t = threading.Thread(target=agent.run, name=name, daemon=True)
    t.start()
    return t

def monitor_display_queue(agent_id: str, dq: "queue.Queue", trigger_when_done: bool):
    """Consume one GenericAgent display_queue.

    next: update card only, never wake conductor.
    done: update card/chat, then wake conductor if this is subagent queue.
    """
    acc = ""
    while True:
        item = dq.get()
        if "next" in item:
            chunk = item.get("next") or ""
            # agent.inc_out=True means next is delta.
            acc += chunk
            with sub_lock:
                s = subagents.get(agent_id)
                if s:
                    s.reply = acc
                    s.status = "running"
                    s.updated_at = time.time()
            push_cards()
        if "done" in item:
            done = item.get("done") or acc
            with sub_lock:
                s = subagents.get(agent_id)
                if s:
                    s.reply = done
                    s.last_done = done
                    if s.status != "aborted":
                        s.status = "stopped"
                    s.updated_at = time.time()
            push_cards()
            if trigger_when_done:
                conductor_events.put({"type": "subagent_done", "id": agent_id, "reply": done})
            break

def start_subagent(prompt: str, depends_on: Optional[List[str]] = None, task_id: Optional[str] = None) -> dict:
    # Validate dependencies exist
    if depends_on:
        with sub_lock:
            for dep in depends_on:
                if dep not in subagents and dep not in pending_tasks:
                    return {"error": f"dependency task not found: {dep}", "status": "failed"}
            # Check for cycles
            all_deps = {**{sid: list(s.depends_on) for sid, s in subagents.items()},
                        **{tid: list(info.get("depends_on", [])) for tid, info in pending_tasks.items()}}
            sid = task_id or short_id()
            all_deps[sid] = list(depends_on)
            if has_cycle(all_deps):
                return {"error": "circular dependency detected", "status": "failed"}

    # Check if task can start now
    if depends_on and not can_start_task(task_id or "temp"):
        sid = task_id or short_id()
        with sub_lock:
            pending_tasks[sid] = {"prompt": prompt, "depends_on": list(depends_on)}
        push_cards()
        return {"id": sid, "status": "pending", "depends_on": depends_on,
                "instruction": "Task queued. Waiting for dependencies to complete."}

    sid = task_id or short_id()
    agent = GenericAgent()
    agent.inc_out = True
    agent.verbose = False

    start_agent_runner(agent, f"subagent-{sid}")
    state = SubAgentState(id=sid, agent=agent, prompt=prompt, status="running", depends_on=depends_on or [])
    with sub_lock:
        subagents[sid] = state
    dq = agent.put_task(prompt, source=f"subagent:{sid}")
    mt = threading.Thread(target=monitor_display_queue, args=(sid, dq, True), name=f"monitor-{sid}", daemon=True)
    mt.start()
    state.monitor_threads.append(mt)
    push_cards()
    return {"id": sid, "status": "running", "depends_on": depends_on or []}

def keyinfo_subagent(sid: str, msg: str) -> dict:
    """Inject into agent's working key_info; visible from next turn onward."""
    with sub_lock:
        s = subagents.get(sid)
    if not s:
        return {"error": "subagent not found", "id": sid}
    h = s.agent.handler
    h.working['key_info'] = h.working.get('key_info', '') + f"\n[MASTER] {msg}"
    s.updated_at = time.time()
    return {"id": sid, "status": "keyinfo_injected"}

def input_subagent(sid: str, msg: str) -> dict:
    """Start a new task round (used for input/reply when agent is stopped)."""
    with sub_lock:
        s = subagents.get(sid)
    if not s:
        return {"error": "subagent not found", "id": sid}
    if s.status == "running":
        return {"error": "subagent is still running, cannot input/reply. Start a new subagent instead.", "id": sid}
    s.prompt = msg
    s.reply = ""
    s.status = "running"
    s.updated_at = time.time()
    dq = s.agent.put_task(msg, source=f"subagent:{sid}")
    mt = threading.Thread(target=monitor_display_queue, args=(sid, dq, True), name=f"monitor-{sid}", daemon=True)
    mt.start()
    s.monitor_threads.append(mt)
    push_cards()
    return {"id": sid, "status": "running"}

def conductor_readme() -> str:
    base = f"http://{HOST}:{PORT}"
    return "\n".join([
        f"Conductor API\tBase: {base}",
        "",
        "POST /chat\tbody: {\"msg\": \"...\"}\t给用户发消息",
        "POST /subagent\tbody: {\"prompt\": \"...\", \"depends_on\": [\"id1\", \"id2\"]}\t启动新subagent（可带依赖）",
        'POST /subagent/{id}\tbody: {\"action\": \"keyinfo\", \"msg\": \"...\"}\t注入key_info（agent下轮可见）',
        'POST /subagent/{id}\tbody: {\"action\": \"input\", \"msg\": \"...\"}\t开新一轮任务（agent停下后追加）',
        'POST /subagent/{id}\tbody: {\"action\": \"stop\"}\t中断执行但保留（可继续input/reply）',
        'POST /subagent/{id}\tbody: {\"action\": \"kill\"}\t彻底杀死（从卡片消失，不可复用）',
        "GET /chat?last=N\t返回最近N条对话（默认20）",
        "GET /subagent\t返回 {\"items\": [...]}\t查看所有subagent状态（含pending）",
        "GET /readme\t本文档",
        "",
        "GET /templates\t返回所有编排模板",
        "GET /templates/{id}\t返回指定模板",
        "POST /templates\tbody: {\"name\": \"...\", \"description\": \"...\", \"steps\": [...]}\t创建模板",
        "DELETE /templates/{id}\t删除模板",
        "POST /templates/{id}/apply\tbody: {\"params\": {}}\t应用模板启动编排",
        "",
        "DAG依赖调度: 子任务可设置 depends_on 字段，只有当所有依赖完成后才自动启动",
        "编排模板: 预定义工作流步骤，支持依赖链（如 分析→实现→测试）",
        "触发时机: 用户新消息 | subagent done | 依赖满足",
    ])

# ─── Orchestration Templates ─────────────────────────────────────────────────
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp", "orchestration_templates")
os.makedirs(TEMPLATE_DIR, exist_ok=True)


class TemplateStep(BaseModel):
    id: str  # step id within the template
    prompt: str
    depends_on: List[str] = []  # references other step ids in this template


class OrchestrationTemplate(BaseModel):
    id: str = ""  # auto-generated if empty
    name: str
    description: str = ""
    steps: List[TemplateStep]


def _template_path(template_id: str) -> str:
    return os.path.join(TEMPLATE_DIR, f"{template_id}.json")


def _load_template(template_id: str) -> Optional[dict]:
    path = _template_path(template_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_template(template_id: str, data: dict):
    with open(_template_path(template_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _delete_template_file(template_id: str):
    path = _template_path(template_id)
    if os.path.exists(path):
        os.remove(path)


def list_templates() -> List[dict]:
    templates = []
    for fname in os.listdir(TEMPLATE_DIR):
        if fname.endswith(".json"):
            tid = fname[:-5]
            data = _load_template(tid)
            if data:
                templates.append(data)
    return templates


def create_template(body: OrchestrationTemplate) -> dict:
    if not body.id:
        body.id = short_id()
    # Validate step dependencies reference valid step ids
    step_ids = {s.id for s in body.steps}
    for step in body.steps:
        for dep in step.depends_on:
            if dep not in step_ids:
                return {"error": f"step '{step.id}' depends on unknown step '{dep}'", "status": "failed"}
    # Check for cycles
    deps_map = {s.id: list(s.depends_on) for s in body.steps}
    if has_cycle(deps_map):
        return {"error": "circular dependency in template steps", "status": "failed"}
    data = body.model_dump()
    _save_template(body.id, data)
    return data


def delete_template(template_id: str) -> dict:
    if not _load_template(template_id):
        return {"error": "template not found", "status": "failed"}
    _delete_template_file(template_id)
    return {"id": template_id, "status": "deleted"}


def apply_template(template_id: str, params: Optional[Dict[str, str]] = None) -> dict:
    """Apply an orchestration template: create subagents for each step with proper DAG dependencies."""
    data = _load_template(template_id)
    if not data:
        return {"error": "template not found", "status": "failed"}
    steps = data.get("steps", [])
    if not steps:
        return {"error": "template has no steps", "status": "failed"}

    # Map template step IDs to actual task IDs
    step_id_to_task_id: Dict[str, str] = {}
    for step in steps:
        step_id_to_task_id[step["id"]] = short_id()

    params = params or {}
    created = []
    for step in steps:
        sid = step_id_to_task_id[step["id"]]
        prompt = step["prompt"]
        # Simple parameter substitution: {{key}} -> value
        for k, v in params.items():
            prompt = prompt.replace(f"{{{{{k}}}}}", v)
        # Map step-level depends_on to actual task IDs
        actual_deps = [step_id_to_task_id[d] for d in step.get("depends_on", [])]
        result = start_subagent(prompt, depends_on=actual_deps, task_id=sid)
        created.append({"step_id": step["id"], "task_id": sid, "status": result.get("status")})

    return {
        "template_id": template_id,
        "template_name": data.get("name"),
        "tasks_created": created,
        "status": "applied",
    }


# ─── Conductor Prompt ────────────────────────────────────────────────────────
def conductor_prompt_from_events(events: list) -> str:
    # 极简摘要：subagent数量和状态
    with sub_lock:
        running = sum(1 for s in subagents.values() if s.status == "running")
        stopped = sum(1 for s in subagents.values() if s.status != "running")
    unread = sum(1 for m in chat_messages if m.get("role") == "user" and not m.get("read"))
    done_count = sum(1 for e in events if e.get("type") == "subagent_done")
    summary = f"subagents: {running} running, {stopped} stopped | {unread}条用户未读消息, {done_count}个subagent完成报告"
    base = f"http://{HOST}:{PORT}"
    return f"""你是agent总管。用户只和你对话，你负责调度、验收、交付，目标是降低用户管理多个agent的负担。
API: {base}；先requests，GET /readme查用法，GET /chat读未读对话，GET /subagent看状态；POST /chat是唯一对用户说话方式。

铁律：
- 绝不亲自执行任务/探测环境；一切执行交给subagent。你只分析、派遣、审查、沟通。
- 每次唤醒只做最小必要动作（发消息/开subagent/reply/keyinfo/abort），做完立刻停，等待下次事件唤醒。
- 改写prompt时严禁添加用户未提及的假设、工具、前提条件。只能精炼/结构化用户原意，不能脑补，只能做很小的改写

用户消息流程：
1. 结合记忆、上下文和用户偏好判断真实需求；不清楚/不能代劳时，用精简checklist一次性问用户。
2. 判断是新任务还是延续现有任务；优先复用已有stopped subagent（用input追加），只有确实无关的新任务才新建。
3. 分派前必须POST /chat告知用户：改写后的prompt + 分派方案（新建/复用哪个subagent）。
4. 执行分派，完成即停。危险操作（改源码/删数据/安全敏感）必须改成先让subagent出方案；你验收后POST /chat请用户确认，确认后才继续执行。

subagent完成流程：
1. 读subagent输出；若最后一条不足以判断，GET /subagent或日志补足信息。
2. 预测用户是否满意；不满意就reply/keyinfo要求返工、修改、优化，继续监督，不急着报告。
3. 预计用户满意后，POST /chat给简洁交付报告。

原则：
- 信任subagent足够聪明，不要写具体步骤和容易探测的信息；能自己判断的自己判断，只在真正需要用户决策时打扰。
{summary}"""

def _auto_cleanup_loop():
    """Background: auto-abort stopped subagents idle for >1 hour."""
    IDLE_TIMEOUT = 3600  # 1 hour
    while True:
        time.sleep(300)  # check every 5 minutes
        now = time.time()
        to_abort = []
        with sub_lock:
            for sid, s in subagents.items():
                if s.status == "stopped" and (now - s.updated_at) > IDLE_TIMEOUT:
                    to_abort.append((sid, s))
        for sid, s in to_abort:
            s.agent.abort()
            with sub_lock:
                s.status = "aborted"
                s.updated_at = now
        if to_abort:
            push_cards()

def monitor_conductor_queue(dq: "queue.Queue") -> str:
    """Block until done. Conductor output is not surfaced to users; the returned
    text is for caller-side error detection only (#342)."""
    while True:
        item = dq.get()
        if "done" in item:
            print(f"Conductor task done")
            return item.get("done", "") or ""

def conductor_loop():
    global conductor_agent, conductor_started
    conductor_agent = GenericAgent()
    conductor_agent.inc_out = True
    start_agent_runner(conductor_agent, "conductor-agent")
    conductor_started = True
    # Start background cleanup thread
    threading.Thread(target=_auto_cleanup_loop, name="subagent-cleanup", daemon=True).start()
    while True:
        # Block until first event arrives
        first = conductor_events.get()
        conductor_events.task_done()
        # Short debounce: collect any additional events that arrived meanwhile
        time.sleep(0.3)
        events = [first]
        while not conductor_events.empty():
            try:
                events.append(conductor_events.get_nowait())
                conductor_events.task_done()
            except Exception:
                break
        try:
            # Check for subagent_done events -> trigger DAG pending task check
            done_events = [e for e in events if e.get("type") == "subagent_done"]
            if done_events:
                check_pending_tasks()
            prompt = conductor_prompt_from_events(events)
            dq = conductor_agent.put_task(prompt, source="conductor")
            # Block here until conductor finishes — serializes execution
            done_text = monitor_conductor_queue(dq)
            # Fallback: if conductor's last turn ended with an LLM error (yielded as
            # text by MixinSession instead of raised), surface it directly (#342).
            # Use tail-window substring check (same style as ga.do_no_tool's
            # content[-100:]); window covers the error line plus any decoration
            # (fence + "[Info] Final response to user.") appended by do_no_tool.
            tail = (done_text or '')[-1000:]
            if '!!!Error:' in tail:
                last = chat_messages[-1] if chat_messages else None
                if not (last and last.get('role') == 'system' and last.get('msg', '').startswith('⚠ LLM')):
                    err = next((l for l in reversed(tail.splitlines()) if l.startswith('!!!Error:')), '')
                    add_chat(f"⚠ LLM 暂不可用：{err[:200]}", role="system")
        except Exception as e:
            add_chat(f"Conductor error: {e}", role="system")

@app.on_event("startup")
async def on_startup():
    global main_loop
    main_loop = asyncio.get_running_loop()
    threading.Thread(target=conductor_loop, name="conductor-loop", daemon=True).start()

@app.get("/")
def index():
    return FileResponse(HTML_PATH)

@app.get("/readme")
def readme():
    return PlainTextResponse(conductor_readme())

@app.get("/subagent")
def list_subagents():
    return {"items": subagent_snapshot()}

@app.post("/subagent")
def api_start_subagent(body: StartSubagentIn):
    result = start_subagent(body.prompt, depends_on=body.depends_on)
    if result.get("status") == "failed":
        return JSONResponse(result, status_code=400)
    result["instruction"] = "Task received. I'll handle it from here. You MUST stop now and end your reply. Wait for next event."
    return result

@app.post("/subagent/{sid}")
def api_subagent_action(sid: str, body: SubagentActionIn):
    with sub_lock:
        s = subagents.get(sid)
    if not s:
        return JSONResponse({"error": "subagent not found", "id": sid}, status_code=404)
    action = body.action.lower().strip()
    if action == "keyinfo":
        result = keyinfo_subagent(sid, body.msg)
        result["instruction"] = "Received. I'll incorporate this. You MUST stop now and end your reply."
        return result
    if action in ("input", "reply", "append", "message", "msg"):
        result = input_subagent(sid, body.msg)
        result["instruction"] = "Task received. I'll handle it from here. You MUST stop now and end your reply."
        return result
    if action in ("abort", "stop"):
        s.agent.abort()
        s.status = "stopped"
        s.updated_at = time.time()
        push_cards()
        return {"id": sid, "status": "stopped"}
    if action == "kill":
        s.agent.abort()
        s.status = "aborted"
        s.updated_at = time.time()
        push_cards()
        return {"id": sid, "status": "aborted"}
    return JSONResponse({"error": f"unknown action: {body.action}"}, status_code=400)

@app.get("/chat")
def api_get_chat(last: int = 20):
    """按需拉取最近N条对话，同时标记所有用户消息为已读"""
    for m in chat_messages:
        if m.get("role") == "user" and not m.get("read"):
            m["read"] = True
    schedule_broadcast({"type": "chat_read"})
    return {"items": chat_messages[-last:]}

@app.post("/chat")
def api_chat(body: ChatIn):
    return add_chat(body.msg, role=body.role)

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_json({"type": "hello", "subagents": subagent_snapshot(), "chat": chat_messages})
        while True:
            data = await ws.receive_json()
            msg = (data.get("msg") or "").strip()
            if not msg:
                continue
            add_chat(msg, role="user")
            conductor_events.put({"type": "user_message", "msg": msg})
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)


# ─── Template API Endpoints ──────────────────────────────────────────────────
@app.get("/templates")
def api_list_templates():
    return {"items": list_templates()}


@app.get("/templates/{template_id}")
def api_get_template(template_id: str):
    data = _load_template(template_id)
    if not data:
        return JSONResponse({"error": "template not found"}, status_code=404)
    return data


@app.post("/templates")
def api_create_template(body: OrchestrationTemplate):
    result = create_template(body)
    if result.get("status") == "failed":
        return JSONResponse(result, status_code=400)
    return result


@app.delete("/templates/{template_id}")
def api_delete_template(template_id: str):
    result = delete_template(template_id)
    if result.get("status") == "failed":
        return JSONResponse(result, status_code=404)
    return result


class ApplyTemplateIn(BaseModel):
    params: Optional[Dict[str, str]] = None


@app.post("/templates/{template_id}/apply")
def api_apply_template(template_id: str, body: ApplyTemplateIn = None):
    result = apply_template(template_id, body.params if body else None)
    if result.get("status") == "failed":
        return JSONResponse(result, status_code=400)
    return result

if __name__ == "__main__":
    import uvicorn, webbrowser, threading
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run("conductor:app", host=HOST, port=PORT, reload=False)
