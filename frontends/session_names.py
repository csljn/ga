"""Persistent display names for `/continue`-able sessions.

JSON sidecar at `temp/model_responses/session_names.json` maps log-file
basename → user name. Touched only by `/rename` and `/continue <name>`.
"""
import glob, json, os, re, threading

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'temp', 'model_responses')
_REG_PATH = os.path.join(_LOG_DIR, 'session_names.json')
_LOG_RE = re.compile(r'^model_responses_(\d+)\.txt$')
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(_REG_PATH, encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)
    tmp = _REG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _REG_PATH)


def _resolve_basename(basename: str):
    # Registered file may be cleared by `continue_cmd._snapshot_current_log`
    # on /new or /continue; fall back to the newest non-empty snapshot of the
    # same PID so a mid-session rename survives the rotation.
    p = os.path.join(_LOG_DIR, basename)
    if os.path.isfile(p) and os.path.getsize(p) > 0:
        return p
    m = _LOG_RE.match(basename)
    if m:
        snaps = glob.glob(os.path.join(_LOG_DIR, f'model_responses_snapshot_{m.group(1)}_*.txt'))
        snaps.sort(key=os.path.getmtime, reverse=True)
        for s in snaps:
            if os.path.getsize(s) > 0:
                return s
    return None


def set_name(log_path: str, name: str) -> None:
    """Persist `name` for `log_path`. Empty name removes the entry."""
    key = os.path.basename(log_path)
    with _lock:
        d = _load()
        if name: d[key] = name
        else: d.pop(key, None)
        _save(d)


def migrate(old_path: str, new_path: str) -> None:
    """Move the entry from old basename to new basename after /continue."""
    if old_path == new_path: return
    old_key, new_key = os.path.basename(old_path), os.path.basename(new_path)
    with _lock:
        d = _load()
        if old_key in d:
            d[new_key] = d.pop(old_key)
            _save(d)


def name_for(log_path: str) -> str:
    return _load().get(os.path.basename(log_path), '')


def has_name(name: str, exclude_basename: str = None) -> bool:
    """True when any other entry already owns `name` (case-insensitive)."""
    target = (name or '').strip().lower()
    if not target: return False
    return any(v.lower() == target for k, v in _load().items() if k != exclude_basename)


def gc() -> int:
    """Drop entries whose log file is gone or empty. Returns count removed."""
    with _lock:
        d = _load()
        bad = [k for k in d if _resolve_basename(k) is None]
        for k in bad: d.pop(k)
        if bad: _save(d)
        return len(bad)


def path_for(name: str, exclude_basename: str = None):
    """Resolve `name` → newest resolvable log path. Exact-match then unique-prefix."""
    target = (name or '').strip().lower()
    if not target: return None
    d = _load()
    matches = [(k, v) for k, v in d.items() if v.lower() == target]
    if not matches:
        matches = [(k, v) for k, v in d.items() if v.lower().startswith(target)]
        if len(matches) > 1: matches = []
    if exclude_basename is not None:
        matches = [m for m in matches if m[0] != exclude_basename]
    resolved = [(p, k) for p, k in ((_resolve_basename(k), k) for k, _ in matches) if p]
    if not resolved: return None
    resolved.sort(key=lambda pk: os.path.getmtime(pk[0]), reverse=True)
    return resolved[0][0]


# ────────────────────────────────────────────────────────────────────────────
# Session management extensions: list / delete / rename / export
# ────────────────────────────────────────────────────────────────────────────

def list_sessions(include_unnamed: bool = False) -> list[dict]:
    """Return all sessions sorted by mtime (newest first).

    Each entry: ``{basename, name, path, mtime, size}``
    - ``name`` is empty string for sessions the user never named.
    - Sessions whose log file is gone/empty are silently skipped.
    """
    d = _load()
    # Also discover unnamed log files on disk
    entries: dict[str, dict] = {}
    if os.path.isdir(_LOG_DIR):
        for fn in os.listdir(_LOG_DIR):
            if _LOG_RE.match(fn) or fn.startswith('model_responses_snapshot_'):
                p = os.path.join(_LOG_DIR, fn)
                if os.path.isfile(p) and os.path.getsize(p) > 0:
                    entries[fn] = {
                        'basename': fn,
                        'name': d.get(fn, ''),
                        'path': p,
                        'mtime': os.path.getmtime(p),
                        'size': os.path.getsize(p),
                    }
    # Overlay registered names
    for k, v in d.items():
        if k in entries:
            entries[k]['name'] = v
        else:
            p = _resolve_basename(k)
            if p:
                entries[k] = {
                    'basename': k,
                    'name': v,
                    'path': p,
                    'mtime': os.path.getmtime(p),
                    'size': os.path.getsize(p),
                }
    if not include_unnamed:
        entries = {k: v for k, v in entries.items() if v['name']}
    result = sorted(entries.values(), key=lambda e: e['mtime'], reverse=True)
    return result


def delete_session(name: str) -> bool:
    """Delete a session entry by name (case-insensitive).

    Returns True if an entry was removed, False if not found.
    The log file itself is NOT deleted — only the registry entry.
    """
    target = (name or '').strip().lower()
    if not target:
        return False
    with _lock:
        d = _load()
        to_remove = [k for k, v in d.items() if v.lower() == target]
        if not to_remove:
            # Try prefix match (unique only)
            to_remove = [k for k, v in d.items() if v.lower().startswith(target)]
            if len(to_remove) != 1:
                return False
        for k in to_remove:
            d.pop(k, None)
        _save(d)
        return bool(to_remove)


def rename_session(old_name: str, new_name: str) -> tuple[bool, str]:
    """Rename a session entry. Returns ``(ok, message)``.

    - ``old_name``: existing name (case-insensitive).
    - ``new_name``: desired new name (must not collide).
    """
    old = (old_name or '').strip().lower()
    new = (new_name or '').strip()
    if not old or not new:
        return False, '名称不能为空'
    with _lock:
        d = _load()
        target_key = None
        for k, v in d.items():
            if v.lower() == old:
                target_key = k
                break
        if target_key is None:
            return False, f'未找到会话 "{old_name}"'
        # Collision check
        for k, v in d.items():
            if k != target_key and v.lower() == new.lower():
                return False, f'名称 "{new}" 已被使用'
        d[target_key] = new
        _save(d)
        return True, f'已重命名为 "{new}"'


def export_session(name: str, fmt: str = 'markdown') -> tuple[bool, str]:
    """Export a session's log content. Returns ``(ok, result_or_error)``.

    ``fmt``: 'markdown' (default) or 'json'.
    """
    log_path = path_for(name)
    if not log_path:
        return False, f'未找到会话 "{name}"'
    try:
        with open(log_path, encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError as e:
        return False, f'读取失败: {e}'
    if fmt == 'json':
        import json as _json
        pairs = _parse_pairs(content)
        return True, _json.dumps(pairs, ensure_ascii=False, indent=2)
    # Default: markdown
    return True, content


def _parse_pairs(content: str) -> list[dict]:
    """Parse model_responses log into request/response pairs (lightweight)."""
    pairs = []
    current_req = None
    current_resp: list[str] = []
    in_response = False
    for line in content.splitlines():
        if line.startswith('=== Request'):
            if current_req is not None:
                pairs.append({'request': current_req, 'response': '\n'.join(current_resp)})
            current_req = ''
            current_resp = []
            in_response = False
        elif line.startswith('=== Response'):
            in_response = True
        elif in_response:
            current_resp.append(line)
        elif current_req is not None:
            current_req += line + '\n'
    if current_req is not None:
        pairs.append({'request': current_req.strip(), 'response': '\n'.join(current_resp)})
    return pairs
