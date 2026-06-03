"""
GenericAgent 更新系统
提供版本检测、差异对比、一键更新功能。
使用 FastAPI 提供 API 接口和前端界面。

启动方式: python frontends/update_app.py [--port 8901]
"""

import os, sys, subprocess, json, time, argparse, threading
from typing import Optional

# 确保能导入上级目录的模块
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

HOST = "127.0.0.1"
PORT = 8901
PROJECT_ROOT = ROOT

app = FastAPI(title="GenericAgent Update System")


# ─── Git 工具函数 ────────────────────────────────────────────────────────────

def _run_git(args: list, cwd: str = None, timeout: int = 30) -> dict:
    """执行 git 命令并返回结果"""
    cwd = cwd or PROJECT_ROOT
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    try:
        result = subprocess.run(
            ['git'] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            startupinfo=startupinfo,
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        return {
            'ok': result.returncode == 0,
            'stdout': result.stdout.strip(),
            'stderr': result.stderr.strip(),
            'returncode': result.returncode,
        }
    except FileNotFoundError:
        return {'ok': False, 'stdout': '', 'stderr': 'git 未安装或不在 PATH 中', 'returncode': -1}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'stdout': '', 'stderr': f'命令超时 ({timeout}s)', 'returncode': -1}
    except Exception as e:
        return {'ok': False, 'stdout': '', 'stderr': str(e), 'returncode': -1}


def is_git_repo() -> bool:
    """检查项目是否是 git 仓库"""
    r = _run_git(['rev-parse', '--is-inside-work-tree'])
    return r['ok'] and r['stdout'] == 'true'


def has_remote() -> bool:
    """检查是否有远程仓库"""
    r = _run_git(['remote'])
    return r['ok'] and bool(r['stdout'].strip())


def get_current_branch() -> str:
    """获取当前分支名"""
    r = _run_git(['branch', '--show-current'])
    return r['stdout'] if r['ok'] else 'unknown'


def get_current_commit() -> dict:
    """获取当前 commit 信息"""
    r = _run_git(['log', '-1', '--format=%H%n%h%n%s%n%ai%n%an'])
    if not r['ok']:
        return {}
    lines = r['stdout'].split('\n', 4)
    if len(lines) < 5:
        return {}
    return {
        'hash': lines[0],
        'short_hash': lines[1],
        'message': lines[2],
        'date': lines[3],
        'author': lines[4],
    }


def check_for_updates() -> dict:
    """检查是否有更新"""
    if not is_git_repo():
        return {'error': '当前目录不是 git 仓库', 'has_updates': False}

    if not has_remote():
        return {'error': '未配置远程仓库', 'has_updates': False}

    # 获取远程更新
    fetch = _run_git(['remote', 'update'], timeout=60)
    if not fetch['ok']:
        return {'error': f'获取远程更新失败: {fetch["stderr"]}', 'has_updates': False}

    # 对比差异
    count_result = _run_git(['rev-list', 'HEAD..@{u}', '--count'])
    if not count_result['ok']:
        # 可能没有设置 upstream
        tracking = _run_git(['rev-parse', '--abbrev-ref', '@{upstream}'])
        if not tracking['ok']:
            return {'error': '当前分支未设置上游跟踪分支，请先执行 git branch --set-upstream-to=origin/main', 'has_updates': False}
        return {'error': f'检查更新失败: {count_result["stderr"]}', 'has_updates': False}

    try:
        commits_behind = int(count_result['stdout'].strip())
    except (ValueError, TypeError):
        commits_behind = 0

    # 获取远程最新 commit
    remote_commit = {}
    r = _run_git(['log', '-1', '--format=%H%n%h%n%s%n%ai%n%an', '@{u}'])
    if r['ok']:
        lines = r['stdout'].split('\n', 4)
        if len(lines) >= 5:
            remote_commit = {
                'hash': lines[0],
                'short_hash': lines[1],
                'message': lines[2],
                'date': lines[3],
                'author': lines[4],
            }

    # 获取落后的 commit 列表
    commit_list = []
    if commits_behind > 0:
        log_result = _run_git(['log', 'HEAD..@{u}', '--format=%h|%s|%ai|%an'])
        if log_result['ok']:
            for line in log_result['stdout'].split('\n'):
                parts = line.split('|', 3)
                if len(parts) >= 4:
                    commit_list.append({
                        'short_hash': parts[0],
                        'message': parts[1],
                        'date': parts[2],
                        'author': parts[3],
                    })

    return {
        'has_updates': commits_behind > 0,
        'commits_behind': commits_behind,
        'current_commit': get_current_commit(),
        'remote_commit': remote_commit,
        'commit_list': commit_list,
        'branch': get_current_branch(),
    }


def get_diff_info() -> dict:
    """获取文件变更和代码差异"""
    if not is_git_repo():
        return {'error': '当前目录不是 git 仓库'}

    # 文件变更列表
    files_result = _run_git(['diff', '--name-status', 'HEAD..@{u}'])
    files = []
    if files_result['ok']:
        for line in files_result['stdout'].split('\n'):
            parts = line.split('\t', 1)
            if len(parts) >= 2:
                status_map = {'A': '新增', 'M': '修改', 'D': '删除', 'R': '重命名'}
                files.append({
                    'status': status_map.get(parts[0][0], parts[0]),
                    'raw_status': parts[0],
                    'file': parts[1],
                })

    # 代码差异 (diff)
    diff_result = _run_git(['diff', 'HEAD..@{u}', '--stat'])
    diff_stat = diff_result['stdout'] if diff_result['ok'] else ''

    # 完整 diff (限制大小)
    full_diff_result = _run_git(['diff', 'HEAD..@{u}'])
    full_diff = full_diff_result['stdout'][:50000] if full_diff_result['ok'] else ''
    if len(full_diff_result.get('stdout', '')) > 50000:
        full_diff += '\n\n... (差异内容过长，已截断)'

    # 潜在冲突检测: 本地有未提交修改的文件
    local_changes = _run_git(['diff', '--name-only'])
    local_modified = local_changes['stdout'].split('\n') if local_changes['ok'] else []
    local_modified = [f for f in local_modified if f.strip()]

    staged = _run_git(['diff', '--cached', '--name-only'])
    staged_files = staged['stdout'].split('\n') if staged['ok'] else []
    staged_files = [f for f in staged_files if f.strip()]

    changed_files = set(f['file'] for f in files)
    conflict_files = [f for f in (local_modified + staged_files) if f in changed_files]

    return {
        'files': files,
        'diff_stat': diff_stat,
        'full_diff': full_diff,
        'local_modified': local_modified,
        'conflict_files': list(set(conflict_files)),
    }


def perform_update(force_remote: bool = False) -> dict:
    """执行更新（git pull --rebase）"""
    if not is_git_repo():
        return {'success': False, 'error': '当前目录不是 git 仓库'}

    if not has_remote():
        return {'success': False, 'error': '未配置远程仓库'}

    # 先 stash 本地修改（如果有）
    stashed = False
    local = _run_git(['status', '--porcelain'])
    has_local_changes = local['ok'] and bool(local['stdout'].strip())

    if has_local_changes:
        if force_remote:
            stash_result = _run_git(['stash', 'push', '-m', 'auto-stash before update'])
            stashed = stash_result['ok']
        else:
            # 检查是否有冲突风险
            diff_info = get_diff_info()
            if diff_info.get('conflict_files'):
                return {
                    'success': False,
                    'error': '检测到本地修改与远程更新存在冲突',
                    'conflict_files': diff_info['conflict_files'],
                    'local_modified': diff_info.get('local_modified', []),
                    'need_force': True,
                }

    # 执行 pull --rebase
    pull_result = _run_git(['pull', '--rebase'], timeout=120)

    if not pull_result['ok']:
        # rebase 失败，尝试 abort
        _run_git(['rebase', '--abort'])
        # 如果之前 stash 了，恢复
        if stashed:
            _run_git(['stash', 'pop'])
        return {
            'success': False,
            'error': f'更新失败: {pull_result["stderr"]}',
            'stdout': pull_result['stdout'],
        }

    # 恢复 stash
    if stashed:
        pop_result = _run_git(['stash', 'pop'])
        if not pop_result['ok']:
            return {
                'success': True,
                'warning': '更新成功但恢复本地修改时出现冲突，请手动解决',
                'stash_conflict': True,
                'output': pull_result['stdout'],
            }

    return {
        'success': True,
        'output': pull_result['stdout'],
        'current_commit': get_current_commit(),
        'branch': get_current_branch(),
    }


def restart_application() -> dict:
    """重启应用（查找并重启主进程）"""
    # 这里只是提供一个重启信号，实际重启由调用方处理
    return {
        'signal': 'restart',
        'message': '更新完成，建议重启应用以加载新代码',
        'pid': os.getpid(),
    }


# ─── API 端点 ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    """返回更新管理界面"""
    return HTMLResponse(UPDATE_HTML)


@app.get("/api/status")
def api_status():
    """获取更新状态"""
    return {
        'is_git_repo': is_git_repo(),
        'has_remote': has_remote(),
        'branch': get_current_branch(),
        'current_commit': get_current_commit(),
    }


@app.get("/api/check")
def api_check():
    """检查是否有更新"""
    return check_for_updates()


@app.get("/api/diff")
def api_diff():
    """获取差异详情"""
    return get_diff_info()


class UpdateRequest(BaseModel):
    force_remote: bool = False


@app.post("/api/update")
def api_update(body: UpdateRequest):
    """执行更新"""
    return perform_update(force_remote=body.force_remote)


@app.post("/api/restart")
def api_restart():
    """请求重启应用"""
    return restart_application()


# ─── 前端 HTML ──────────────────────────────────────────────────────────────

UPDATE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GenericAgent 更新管理</title>
  <style>
    :root {
      --bg: #0f1117;
      --surface: #1a1d27;
      --surface-2: #242836;
      --border: #2e3348;
      --text: #e4e8f1;
      --text-muted: #8b92a8;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --green: #10b981;
      --green-bg: rgba(16, 185, 129, 0.12);
      --red: #ef4444;
      --red-bg: rgba(239, 68, 68, 0.12);
      --amber: #f59e0b;
      --amber-bg: rgba(245, 158, 11, 0.12);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 32px;
    }
    .container { max-width: 900px; margin: 0 auto; }
    h1 {
      font-size: 24px;
      font-weight: 600;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    h1 .icon { font-size: 28px; }
    .subtitle { color: var(--text-muted); margin-bottom: 32px; font-size: 14px; }

    /* 状态卡片 */
    .status-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 20px;
    }
    .status-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }
    .status-title { font-size: 16px; font-weight: 600; }
    .badge {
      padding: 4px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 500;
    }
    .badge-green { background: var(--green-bg); color: var(--green); }
    .badge-amber { background: var(--amber-bg); color: var(--amber); }
    .badge-red { background: var(--red-bg); color: var(--red); }

    .info-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }
    .info-item { font-size: 13px; }
    .info-label { color: var(--text-muted); margin-bottom: 2px; }
    .info-value { font-family: "SF Mono", "Fira Code", monospace; font-size: 12px; }

    /* commit 列表 */
    .commit-list { margin-top: 16px; }
    .commit-item {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--border);
      font-size: 13px;
    }
    .commit-item:last-child { border-bottom: none; }
    .commit-hash {
      font-family: "SF Mono", "Fira Code", monospace;
      color: var(--accent);
      font-size: 12px;
      white-space: nowrap;
    }
    .commit-msg { flex: 1; }
    .commit-meta { color: var(--text-muted); font-size: 11px; white-space: nowrap; }

    /* 文件列表 */
    .file-list { margin-top: 12px; }
    .file-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 0;
      font-size: 13px;
      font-family: "SF Mono", "Fira Code", monospace;
    }
    .file-status {
      display: inline-block;
      width: 20px;
      height: 20px;
      border-radius: 4px;
      text-align: center;
      line-height: 20px;
      font-size: 11px;
      font-weight: 600;
    }
    .file-status-A { background: var(--green-bg); color: var(--green); }
    .file-status-M { background: var(--amber-bg); color: var(--amber); }
    .file-status-D { background: var(--red-bg); color: var(--red); }
    .file-status-R { background: rgba(59, 130, 246, 0.12); color: var(--accent); }

    /* 差异展示 */
    .diff-container {
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      margin-top: 12px;
      max-height: 400px;
      overflow-y: auto;
      font-family: "SF Mono", "Fira Code", monospace;
      font-size: 12px;
      line-height: 1.6;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .diff-add { color: #3fb950; }
    .diff-del { color: #f85149; }
    .diff-hdr { color: #79c0ff; font-weight: 600; }

    /* 按钮 */
    .btn-group { display: flex; gap: 10px; margin-top: 24px; flex-wrap: wrap; }
    .btn {
      padding: 10px 20px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--surface-2);
      color: var(--text);
      font-size: 14px;
      cursor: pointer;
      transition: all 0.15s;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .btn:hover { background: var(--border); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .btn-primary:hover { background: var(--accent-hover); }
    .btn-danger {
      background: var(--red-bg);
      border-color: rgba(239, 68, 68, 0.3);
      color: var(--red);
    }

    /* 输出区 */
    .output-area {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      margin-top: 20px;
      display: none;
    }
    .output-area.visible { display: block; }
    .output-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
    .output-content {
      font-family: "SF Mono", "Fira Code", monospace;
      font-size: 12px;
      line-height: 1.6;
      white-space: pre-wrap;
      color: var(--text-muted);
    }

    /* 冲突警告 */
    .conflict-warning {
      background: var(--red-bg);
      border: 1px solid rgba(239, 68, 68, 0.3);
      border-radius: 8px;
      padding: 16px;
      margin-top: 12px;
      display: none;
    }
    .conflict-warning.visible { display: block; }
    .conflict-warning h4 { color: var(--red); margin-bottom: 8px; font-size: 14px; }
    .conflict-warning ul { padding-left: 20px; font-size: 13px; }

    /* Loading */
    .spinner {
      display: inline-block;
      width: 16px;
      height: 16px;
      border: 2px solid var(--border);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* tab 切换 */
    .tabs { display: flex; gap: 0; margin-bottom: 20px; }
    .tab {
      padding: 10px 20px;
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--text-muted);
      cursor: pointer;
      font-size: 13px;
      transition: all 0.15s;
    }
    .tab:first-child { border-radius: 8px 0 0 8px; }
    .tab:last-child { border-radius: 0 8px 8px 0; }
    .tab.active {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
  </style>
</head>
<body>
  <div class="container">
    <h1><span class="icon">&#x21BB;</span> GenericAgent 更新管理</h1>
    <p class="subtitle">版本检测 &middot; 差异对比 &middot; 一键更新</p>

    <!-- 状态卡片 -->
    <div class="status-card" id="statusCard">
      <div class="status-header">
        <span class="status-title">当前状态</span>
        <span class="badge" id="statusBadge">检测中...</span>
      </div>
      <div class="info-grid" id="statusInfo">
        <div class="info-item">
          <div class="info-label">分支</div>
          <div class="info-value" id="branch">-</div>
        </div>
        <div class="info-item">
          <div class="info-label">当前版本</div>
          <div class="info-value" id="currentHash">-</div>
        </div>
        <div class="info-item">
          <div class="info-label">提交信息</div>
          <div class="info-value" id="currentMsg">-</div>
        </div>
        <div class="info-item">
          <div class="info-label">提交时间</div>
          <div class="info-value" id="currentDate">-</div>
        </div>
      </div>
    </div>

    <!-- 更新信息 -->
    <div class="status-card" id="updateCard" style="display:none">
      <div class="status-header">
        <span class="status-title">可用更新</span>
        <span class="badge badge-amber" id="commitsBehind">0 个提交</span>
      </div>

      <div class="tabs">
        <div class="tab active" data-tab="commits">提交记录</div>
        <div class="tab" data-tab="files">文件变更</div>
        <div class="tab" data-tab="diff">代码差异</div>
      </div>

      <div class="tab-content active" id="tab-commits">
        <div class="commit-list" id="commitList"></div>
      </div>

      <div class="tab-content" id="tab-files">
        <div class="file-list" id="fileList"></div>
      </div>

      <div class="tab-content" id="tab-diff">
        <div class="diff-container" id="diffContent">加载中...</div>
      </div>
    </div>

    <!-- 冲突警告 -->
    <div class="conflict-warning" id="conflictWarning">
      <h4>&#x26A0; 检测到潜在冲突</h4>
      <p style="margin-bottom:8px;font-size:13px;">以下文件有本地修改，可能与远程更新冲突：</p>
      <ul id="conflictFiles"></ul>
    </div>

    <!-- 操作按钮 -->
    <div class="btn-group">
      <button class="btn" id="btnCheck" onclick="checkUpdates()">
        <span id="checkIcon">&#x1F50D;</span> 检查更新
      </button>
      <button class="btn btn-primary" id="btnUpdate" onclick="doUpdate(false)" disabled>
        &#x2B06; 拉取更新
      </button>
      <button class="btn btn-danger" id="btnForce" onclick="doUpdate(true)" disabled>
        &#x26A0; 强制更新（覆盖本地）
      </button>
    </div>

    <!-- 输出区域 -->
    <div class="output-area" id="outputArea">
      <div class="output-title" id="outputTitle">操作结果</div>
      <div class="output-content" id="outputContent"></div>
    </div>
  </div>

  <script>
    const API = '';

    // Tab 切换
    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const group = tab.parentElement;
        group.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const content = tab.closest('.status-card');
        content.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        content.querySelector('#tab-' + tab.dataset.tab).classList.add('active');
      });
    });

    function showOutput(title, content, isError) {
      const area = document.getElementById('outputArea');
      area.classList.add('visible');
      document.getElementById('outputTitle').textContent = title;
      const el = document.getElementById('outputContent');
      el.textContent = content;
      el.style.color = isError ? 'var(--red)' : 'var(--text-muted)';
    }

    function escapeHtml(s) {
      return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function formatDiff(diffText) {
      return diffText.split('\\n').map(line => {
        if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff '))
          return '<span class="diff-hdr">' + escapeHtml(line) + '</span>';
        if (line.startsWith('+')) return '<span class="diff-add">' + escapeHtml(line) + '</span>';
        if (line.startsWith('-')) return '<span class="diff-del">' + escapeHtml(line) + '</span>';
        if (line.startsWith('@@')) return '<span class="diff-hdr">' + escapeHtml(line) + '</span>';
        return escapeHtml(line);
      }).join('\\n');
    }

    async function checkUpdates() {
      const btn = document.getElementById('btnCheck');
      const icon = document.getElementById('checkIcon');
      btn.disabled = true;
      icon.innerHTML = '<span class="spinner"></span>';

      try {
        // 先获取状态
        const statusRes = await fetch(API + '/api/status');
        const status = await statusRes.json();

        document.getElementById('branch').textContent = status.branch || '-';
        if (status.current_commit) {
          document.getElementById('currentHash').textContent = status.current_commit.short_hash || '-';
          document.getElementById('currentMsg').textContent = status.current_commit.message || '-';
          document.getElementById('currentDate').textContent = status.current_commit.date || '-';
        }

        if (!status.is_git_repo) {
          document.getElementById('statusBadge').textContent = '非 Git 仓库';
          document.getElementById('statusBadge').className = 'badge badge-red';
          showOutput('错误', '当前目录不是 git 仓库，请先初始化 git', true);
          return;
        }

        if (!status.has_remote) {
          document.getElementById('statusBadge').textContent = '无远程仓库';
          document.getElementById('statusBadge').className = 'badge badge-amber';
          showOutput('提示', '未配置远程仓库，请先添加 remote', false);
          return;
        }

        // 检查更新
        const checkRes = await fetch(API + '/api/check');
        const check = await checkRes.json();

        if (check.error) {
          document.getElementById('statusBadge').textContent = '检查失败';
          document.getElementById('statusBadge').className = 'badge badge-red';
          showOutput('检查失败', check.error, true);
          return;
        }

        if (check.has_updates) {
          document.getElementById('statusBadge').textContent = '有可用更新';
          document.getElementById('statusBadge').className = 'badge badge-amber';
          document.getElementById('updateCard').style.display = 'block';
          document.getElementById('commitsBehind').textContent = check.commits_behind + ' 个提交';
          document.getElementById('btnUpdate').disabled = false;
          document.getElementById('btnForce').disabled = false;

          // 填充 commit 列表
          const listEl = document.getElementById('commitList');
          listEl.innerHTML = check.commit_list.map(c =>
            '<div class="commit-item">' +
              '<span class="commit-hash">' + escapeHtml(c.short_hash) + '</span>' +
              '<span class="commit-msg">' + escapeHtml(c.message) + '</span>' +
              '<span class="commit-meta">' + escapeHtml(c.author) + ' &middot; ' + escapeHtml(c.date) + '</span>' +
            '</div>'
          ).join('');

          // 获取差异
          const diffRes = await fetch(API + '/api/diff');
          const diff = await diffRes.json();

          if (diff.files) {
            document.getElementById('fileList').innerHTML = diff.files.map(f =>
              '<div class="file-item">' +
                '<span class="file-status file-status-' + f.raw_status[0] + '">' + f.raw_status[0] + '</span>' +
                '<span>' + escapeHtml(f.file) + '</span>' +
              '</div>'
            ).join('');
          }

          if (diff.full_diff) {
            document.getElementById('diffContent').innerHTML = formatDiff(diff.full_diff);
          } else if (diff.diff_stat) {
            document.getElementById('diffContent').textContent = diff.diff_stat;
          }

          if (diff.conflict_files && diff.conflict_files.length > 0) {
            document.getElementById('conflictWarning').classList.add('visible');
            document.getElementById('conflictFiles').innerHTML =
              diff.conflict_files.map(f => '<li><code>' + escapeHtml(f) + '</code></li>').join('');
          }
        } else {
          document.getElementById('statusBadge').textContent = '已是最新';
          document.getElementById('statusBadge').className = 'badge badge-green';
          document.getElementById('updateCard').style.display = 'none';
          showOutput('检查完成', '当前已是最新版本 (' + (status.current_commit?.short_hash || 'unknown') + ')', false);
        }
      } catch (e) {
        document.getElementById('statusBadge').textContent = '检查失败';
        document.getElementById('statusBadge').className = 'badge badge-red';
        showOutput('网络错误', e.message, true);
      } finally {
        btn.disabled = false;
        icon.textContent = '\\u1F50D';
      }
    }

    async function doUpdate(force) {
      const btnId = force ? 'btnForce' : 'btnUpdate';
      const btn = document.getElementById(btnId);
      const origText = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> 更新中...';

      try {
        const res = await fetch(API + '/api/update', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({force_remote: force}),
        });
        const data = await res.json();

        if (data.success) {
          showOutput('更新成功',
            (data.output || '') + '\\n\\n当前版本: ' + (data.current_commit?.short_hash || ''),
            false
          );
          // 刷新状态
          setTimeout(checkUpdates, 1000);
        } else if (data.need_force) {
          document.getElementById('conflictWarning').classList.add('visible');
          document.getElementById('conflictFiles').innerHTML =
            (data.conflict_files || []).map(f => '<li><code>' + escapeHtml(f) + '</code></li>').join('');
          showOutput('更新中止', data.error + '\\n请使用"强制更新"覆盖本地修改，或手动解决冲突后重试', true);
        } else {
          showOutput('更新失败', data.error || '未知错误', true);
        }
      } catch (e) {
        showOutput('网络错误', e.message, true);
      } finally {
        btn.disabled = false;
        btn.innerHTML = origText;
      }
    }

    // 页面加载时自动检查
    window.addEventListener('DOMContentLoaded', checkUpdates);
  </script>
</body>
</html>"""


# ─── 启动 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GenericAgent Update System")
    parser.add_argument("--host", default=HOST, help=f"监听地址 (默认: {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"监听端口 (默认: {PORT})")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    import uvicorn
    import webbrowser

    print(f"GenericAgent 更新系统启动中...")
    print(f"访问地址: http://{args.host}:{args.port}")

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
