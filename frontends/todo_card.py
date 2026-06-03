"""Floating todo card UI component for GenericAgent.

A self-contained Streamlit component that provides:
  - Todo item display as interactive cards
  - Manual task addition via text input
  - One-click execution (submit to agent via put_task)
  - Task completion toggle (checkbox)
  - Drag-and-drop reordering (via HTML5 sortable)

Usage in stapp.py:
    from todo_card import render_todo_card
    render_todo_card(agent)
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

import streamlit as st

# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------
_CARD_KEY = "__todo_card__"
_ITEMS_KEY = "todo_items"
_INPUT_KEY = "todo_input"
# Each item: {"id": str, "text": str, "done": bool, "ts": float}


def _init_state():
    """Ensure session_state keys exist."""
    if _ITEMS_KEY not in st.session_state:
        st.session_state[_ITEMS_KEY] = []


def _gen_id() -> str:
    return f"t_{int(time.time()*1000)}"


# ---------------------------------------------------------------------------
# HTML / JS for sortable cards (no external deps)
# ---------------------------------------------------------------------------
_SORTABLE_JS = """
<script>
(function() {
    const container = window.parent.document.querySelector('#todo-card-list');
    if (!container || container.__sortable_init) return;
    container.__sortable_init = true;
    let dragEl = null;
    container.addEventListener('dragstart', e => {
        dragEl = e.target.closest('.todo-card-item');
        if (dragEl) { dragEl.style.opacity = '0.4'; e.dataTransfer.effectAllowed = 'move'; }
    });
    container.addEventListener('dragend', e => {
        if (dragEl) dragEl.style.opacity = '';
        dragEl = null;
    });
    container.addEventListener('dragover', e => {
        e.preventDefault();
        const target = e.target.closest('.todo-card-item');
        if (target && target !== dragEl) {
            const rect = target.getBoundingClientRect();
            const mid = rect.top + rect.height / 2;
            if (e.clientY < mid) container.insertBefore(dragEl, target);
            else container.insertBefore(dragEl, target.nextSibling);
        }
    });
    container.addEventListener('drop', e => {
        e.preventDefault();
        // Read new order from DOM
        const ids = Array.from(container.querySelectorAll('.todo-card-item'))
                         .map(el => el.dataset.id);
        // Send to Streamlit
        window.parent.postMessage({type: 'todo_reorder', ids: ids}, '*');
    });
})();
</script>
"""

_CSS = """
<style>
.todo-card-wrapper {
    position: fixed;
    bottom: 80px;
    right: 24px;
    width: 320px;
    max-height: 480px;
    background: #1e1e2e;
    border: 1px solid #444;
    border-radius: 12px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    z-index: 9999;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
.todo-card-header {
    padding: 10px 14px;
    background: #2d2d3f;
    color: #e0e0e0;
    font-size: 14px;
    font-weight: 600;
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    user-select: none;
}
.todo-card-header .badge {
    background: #6c5ce7;
    color: #fff;
    border-radius: 10px;
    padding: 1px 8px;
    font-size: 12px;
    font-weight: 500;
}
.todo-card-body {
    padding: 8px 0;
    max-height: 320px;
    overflow-y: auto;
}
.todo-card-item {
    display: flex;
    align-items: flex-start;
    padding: 6px 14px;
    gap: 8px;
    cursor: grab;
    transition: background 0.15s;
}
.todo-card-item:hover {
    background: rgba(108,92,231,0.08);
}
.todo-card-item[draggable="true"] { cursor: grab; }
.todo-card-item .cb {
    margin-top: 3px;
    flex-shrink: 0;
}
.todo-card-item .txt {
    flex: 1;
    color: #d0d0d0;
    font-size: 13px;
    line-height: 1.4;
    word-break: break-word;
}
.todo-card-item .txt.done {
    text-decoration: line-through;
    opacity: 0.5;
}
.todo-card-item .exec-btn {
    background: #6c5ce7;
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    cursor: pointer;
    flex-shrink: 0;
    opacity: 0.8;
}
.todo-card-item .exec-btn:hover { opacity: 1; }
.todo-card-item .del-btn {
    background: none;
    border: none;
    color: #888;
    cursor: pointer;
    font-size: 14px;
    padding: 0 2px;
    flex-shrink: 0;
}
.todo-card-item .del-btn:hover { color: #e74c3c; }
.todo-card-input {
    display: flex;
    padding: 8px 14px;
    gap: 6px;
    border-top: 1px solid #333;
}
.todo-card-input input {
    flex: 1;
    background: #2d2d3f;
    border: 1px solid #444;
    border-radius: 6px;
    color: #e0e0e0;
    padding: 6px 10px;
    font-size: 13px;
    outline: none;
}
.todo-card-input input:focus { border-color: #6c5ce7; }
.todo-card-input button {
    background: #6c5ce7;
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
    cursor: pointer;
}
.todo-card-input button:hover { background: #5b4bd5; }
</style>
"""


# ---------------------------------------------------------------------------
# Core render function
# ---------------------------------------------------------------------------
def render_todo_card(agent, *, collapsed: bool = False):
    """Render the floating todo card in a Streamlit app.

    Args:
        agent: GenericAgent instance (needs .put_task method).
        collapsed: Start with card body hidden.
    """
    _init_state()
    items: list[dict] = st.session_state[_ITEMS_KEY]

    # --- Inject CSS + sortable JS ---
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(_SORTABLE_JS, unsafe_allow_html=True)

    # --- Session-state toggle for collapse ---
    collapse_key = f"{_CARD_KEY}_collapsed"
    if collapse_key not in st.session_state:
        st.session_state[collapse_key] = collapsed

    # --- Header ---
    n_open = sum(1 for i in items if not i["done"])
    n_total = len(items)
    badge = f"{n_open}/{n_total}" if n_total else "0"

    st.markdown(
        f'<div class="todo-card-wrapper">'
        f'<div class="todo-card-header" onclick="'
        f"window.parent.document.querySelector('.todo-card-body').style.display="
        f"window.parent.document.querySelector('.todo-card-body').style.display==='none'?'block':'none'"
        f'">'
        f'<span>📋 待办事项</span>'
        f'<span class="badge">{badge}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # --- Items ---
    body_html = '<div class="todo-card-body">'
    for item in items:
        iid = item["id"]
        txt = item["text"]
        done = item["done"]
        cls = "done" if done else ""
        chk = "checked" if done else ""
        body_html += (
            f'<div class="todo-card-item" draggable="true" data-id="{iid}">'
            f'<input type="checkbox" class="cb" {chk} data-cb="{iid}">'
            f'<span class="txt {cls}">{txt}</span>'
        )
        if not done:
            body_html += (
                f'<button class="exec-btn" data-exec="{iid}" title="提交执行">▶</button>'
            )
        body_html += f'<button class="del-btn" data-del="{iid}" title="删除">×</button>'
        body_html += '</div>'
    body_html += '</div>'
    st.markdown(body_html, unsafe_allow_html=True)

    # --- Input area ---
    st.markdown(
        '<div class="todo-card-input">'
        f'<input type="text" id="todo-new-input" placeholder="添加新任务..." />'
        f'<button id="todo-add-btn">添加</button>'
        '</div></div>',
        unsafe_allow_html=True,
    )

    # --- Streamlit-side controls (hidden checkboxes / buttons for state mutation) ---
    # We use st.columns for the actual Streamlit interactive controls below the card.
    _render_streamlit_controls(agent, items)


def _render_streamlit_controls(agent, items: list[dict]):
    """Render hidden Streamlit widgets for state management + interaction callbacks."""
    # --- Manual add ---
    with st.container():
        col_input, col_btn = st.columns([4, 1])
        with col_input:
            new_text = st.text_input(
                "添加任务",
                key=_INPUT_KEY,
                placeholder="输入新任务...",
                label_visibility="collapsed",
            )
        with col_btn:
            if st.button("➕", key=f"{_CARD_KEY}_add", help="添加任务"):
                if new_text and new_text.strip():
                    items.append({
                        "id": _gen_id(),
                        "text": new_text.strip(),
                        "done": False,
                        "ts": time.time(),
                    })
                    st.session_state[_INPUT_KEY] = ""
                    st.rerun()

    # --- Per-item controls ---
    to_remove: list[str] = []
    to_execute: list[str] = []
    for item in items:
        iid = item["id"]
        cols = st.columns([0.5, 3, 1, 1])
        with cols[0]:
            checked = st.checkbox(
                "", value=item["done"], key=f"{_CARD_KEY}_cb_{iid}",
                label_visibility="collapsed",
            )
            if checked != item["done"]:
                item["done"] = checked
                st.rerun()
        with cols[1]:
            st.caption(item["text"])
        with cols[2]:
            if not item["done"] and st.button("▶", key=f"{_CARD_KEY}_exec_{iid}", help="提交执行"):
                to_execute.append(iid)
        with cols[3]:
            if st.button("×", key=f"{_CARD_KEY}_del_{iid}", help="删除"):
                to_remove.append(iid)

    # --- Process deletions ---
    if to_remove:
        st.session_state[_ITEMS_KEY] = [
            i for i in items if i["id"] not in to_remove
        ]
        st.rerun()

    # --- Process executions ---
    for iid in to_execute:
        target = next((i for i in items if i["id"] == iid), None)
        if target:
            _execute_task(agent, target["text"])
            target["done"] = True
            st.rerun()

    # --- Bulk actions ---
    if items:
        col_clear, col_export = st.columns(2)
        with col_clear:
            if st.button("🗑️ 清除已完成", key=f"{_CARD_KEY}_clear_done"):
                st.session_state[_ITEMS_KEY] = [
                    i for i in items if not i["done"]
                ]
                st.rerun()
        with col_export:
            if st.button("📋 导出为计划", key=f"{_CARD_KEY}_export"):
                _export_to_plan(items)


def _execute_task(agent, text: str):
    """Submit a task to the agent via put_task."""
    try:
        dq = agent.put_task(text, source="todo_card")
        # Fire-and-forget; the main app loop will handle the display queue.
        st.toast(f"✅ 任务已提交: {text[:30]}")
    except Exception as e:
        st.error(f"❌ 提交失败: {e}")


def _export_to_plan(items: list[dict]):
    """Export current todo items as a plan.md formatted string and copy to clipboard."""
    lines = ["# TODO 计划\n"]
    for item in items:
        mark = "x" if item["done"] else " "
        lines.append(f"- [{mark}] {item['text']}")
    content = "\n".join(lines)
    st.code(content, language="markdown")
    st.toast("📋 已导出为计划格式")


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    st.set_page_config(page_title="Todo Card Demo", layout="wide")
    st.title("📋 Todo Card Demo")

    # Mock agent for testing
    class MockAgent:
        def put_task(self, text, **kw):
            print(f"[MOCK] put_task: {text}")
            return None

    # Add some demo items if empty
    if not st.session_state.get(_ITEMS_KEY):
        st.session_state[_ITEMS_KEY] = [
            {"id": _gen_id(), "text": "示例任务：检查代码质量", "done": False, "ts": time.time()},
            {"id": _gen_id(), "text": "示例任务：编写单元测试", "done": True, "ts": time.time()},
            {"id": _gen_id(), "text": "示例任务：部署到生产环境", "done": False, "ts": time.time()},
        ]

    render_todo_card(MockAgent())
