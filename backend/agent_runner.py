"""Bounded JSON-lines script runner with cooperative cancellation and tree cleanup."""
from __future__ import annotations
import atexit
from collections import deque
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading
import time
import uuid

_registry = {}
_lock = threading.Lock()
MAX_LINE = 65536


class _WindowsJob:
    def __init__(self, proc):
        self.handle = None
        if os.name != 'nt': return
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [('ProcessTime', ctypes.c_longlong), ('JobTime', ctypes.c_longlong), ('Flags', wintypes.DWORD), ('Min', ctypes.c_size_t), ('Max', ctypes.c_size_t), ('Active', wintypes.DWORD), ('Affinity', ctypes.c_size_t), ('Priority', wintypes.DWORD), ('Scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('IO', IO), ('ProcessMemory', ctypes.c_size_t), ('JobMemory', ctypes.c_size_t), ('PeakProcess', ctypes.c_size_t), ('PeakJob', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        info = Extended(); info.Basic.Flags = 0x2000  # KILL_ON_JOB_CLOSE
        # A child spawned before assignment can escape; use suspended creation if this ever matters.
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(proc._handle)):
            self.close()
            raise OSError(ctypes.get_last_error(), 'Cannot assign agent process to Windows Job Object')
    def kill(self):
        if self.handle: self.kernel.TerminateJobObject(self.handle, 1)
    def close(self):
        if self.handle: self.kernel.CloseHandle(self.handle); self.handle = None


def _signal(proc, job, force=False):
    try:
        if os.name == 'nt':
            if force:
                if job: job.kill()
                else: proc.kill()
            else: os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        else: os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, OSError): pass


def stop_session_scripts(session_id):
    with _lock: entries = list(_registry.values())
    for sid, event, proc, job in entries:
        if sid == session_id: event.set()


def _shutdown():
    with _lock: entries = list(_registry.values())
    for _, event, proc, job in entries:
        event.set()
        _signal(proc, job)
    until = time.monotonic() + 1
    for _, _, proc, job in entries:
        try: proc.wait(timeout=max(.01, until-time.monotonic()))
        except subprocess.TimeoutExpired: pass
        _signal(proc, job, True)
atexit.register(_shutdown)


def run_agent_script(profile, params, dry_run, *, cwd, run_id, cancel_event, emit, tool_call_id=None, scratch_dir=None):
    from agent_profiles import validate_params
    values, errors = validate_params(profile, params)
    if errors: return 'ERROR: ' + '; '.join(errors)
    if profile.errors: return 'ERROR: ' + '; '.join(profile.errors)
    if not profile.entry: return 'ERROR: Agent has no entry script'
    entry = (profile.path / profile.entry).resolve()
    if not entry.is_file() or not entry.is_relative_to(profile.path.resolve()): return 'ERROR: Agent entry escaped its folder or is missing'
    if dry_run and not profile.dry_run: return 'ERROR: Dry run is not supported'
    call_id = tool_call_id or 'script_' + uuid.uuid4().hex[:12]
    log_tail = deque(maxlen=40)
    items = {}
    final = {}
    local_stop = threading.Event()
    pending_progress = None
    last_progress = 0
    warned = set()
    def log(text):
        log_tail.append(str(text)[:MAX_LINE])
        while sum(len(line.encode('utf-8')) for line in log_tail) > 2 * 1024 * 1024:
            log_tail.popleft()
    def warn(key):
        if key not in warned: warned.add(key); log('Protocol limit/invalid record: ' + key)
    def event(kind, data): emit('agent_run_' + kind, dict(data, tool_call_id=call_id))
    def record(line, stderr=False):
        nonlocal final, pending_progress, last_progress
        if stderr: log(line); return
        try: data = json.loads(line)
        except ValueError: log(line); return
        if not isinstance(data, dict) or not isinstance(data.get('wj'), str): log(line); return
        kind = data.pop('wj')
        for key in ('label','detail','summary','markdown','text'):
            if key in data and data[key] is not None and not isinstance(data[key], str): warn(key); return
        if 'data' in data and not isinstance(data['data'],dict): warn('item data'); return
        if kind == 'progress':
            if type(data.get('done')) is not int or data.get('done', -1) < 0 or (data.get('total') is not None and (type(data['total']) is not int or data['total'] < 0)): warn('progress'); return
            pending_progress = {k: data.get(k) for k in ('done','total','label')}
            if time.monotonic() - last_progress >= .25:
                event('progress', pending_progress); pending_progress = None; last_progress = time.monotonic()
        elif kind == 'item':
            key = data.get('key')
            if not isinstance(key, str) or not isinstance(data.get('status'), str) or data.get('status') not in {'pending','running','ok','failed','skipped','info'}: warn('item'); return
            if key not in items and len(items) >= 500: warn('500 items'); return
            data = {k: v for k, v in data.items() if k in {'key','status','label','detail','data'}}
            items[key] = data; event('item', data)
        elif kind == 'result':
            if not isinstance(data.get('summary'), str): warn('result'); return
            table = data.get('table')
            if table is not None and (not isinstance(table, dict) or not isinstance(table.get('columns'), list) or not isinstance(table.get('rows'), list) or any(not isinstance(row,list) for row in table.get('rows',[]))): warn('table'); table = None
            if table and len(table['rows']) > 1000: warn('1000 rows'); table['rows'] = table['rows'][:1000]
            final = {'summary': data['summary'], 'table': table, 'markdown': str(data.get('markdown') or '')[:100000]}
        elif kind == 'log': log(data.get('text', ''))
        else: warn('unknown record')
    events = queue.Queue(maxsize=256)
    def reader(stream, stderr):
        try:
            while True:
                line = stream.readline(MAX_LINE + 1)
                if not line: break
                if len(line) > MAX_LINE:
                    while line and not line.endswith(b'\n'): line = stream.readline(MAX_LINE + 1)
                    events.put(('warning', '64 KB line')); continue
                events.put((stderr, line.decode('utf-8', errors='replace').rstrip()))
        finally: stream.close(); events.put(('end', stderr))
    status = 'failed'; proc = None; job = None
    with tempfile.TemporaryDirectory(prefix='mh-agent-', dir=scratch_dir) as scratch:
        param_file = Path(scratch) / 'params.json'; param_file.write_text(json.dumps(values), encoding='utf-8')
        env = os.environ.copy()
        env.update(MYHARNESS_AGENT='1', MYHARNESS_AGENT_NAME=profile.name, MYHARNESS_AGENT_DIR=str(profile.path), MYHARNESS_RUN_ID=run_id, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
        from agent_profiles import helper_lib_dir
        helper = str(helper_lib_dir())
        env['PYTHONPATH'] = helper + os.pathsep + env.get('PYTHONPATH', '')
        import utils
        import skill_registry
        env['MYHARNESS_ALLOWED_PATHS'] = json.dumps(utils.ALLOWED_PATHS)
        env['MYHARNESS_CHART_JS_URL'] = utils.CHART_JS_URL
        env['MYHARNESS_SKILL_DIRS'] = json.dumps({s.name:str(s.path.parent) for s in skill_registry.list_skills() if s.name in profile.uses_skills})
        argv = [profile.python or __import__('sys').executable, str(entry), '--params', str(param_file)] + (['--dry-run'] if dry_run else [])
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}))
            job = _WindowsJob(proc)
            with _lock: _registry[call_id] = (run_id, local_stop, proc, job)
            threads = [threading.Thread(target=reader, args=(stream, err), daemon=True) for stream, err in [(proc.stdout,False),(proc.stderr,True)]]
            for thread in threads: thread.start()
            start = time.monotonic(); deadline = None; ended = 0
            while ended < 2 or proc.poll() is None:
                now = time.monotonic()
                if deadline is None and (cancel_event.is_set() or local_stop.is_set() or now - start >= profile.timeout_seconds):
                    status = 'stopped' if cancel_event.is_set() or local_stop.is_set() else 'timed_out'
                    _signal(proc, job); deadline = now + profile.on_stop.get('grace_seconds', 5)
                if deadline is not None and now >= deadline:
                    _signal(proc, job, True)
                    # Descendant-held pipe descriptors must not keep this loop alive indefinitely.
                    if now > deadline + 2: break
                if proc.poll() is not None and deadline is None:
                    status = 'completed' if proc.returncode == 0 else 'failed'
                    _signal(proc, job, True); deadline = now + 2
                try:
                    kind, line = events.get(timeout=.1)
                    if kind == 'end': ended += 1
                    elif kind == 'warning': warn(line)
                    else: record(line, kind)
                except queue.Empty: pass
                # A throttled update must not wait for the next record, which
                # may be minutes away while a long target runs.
                if pending_progress and time.monotonic() - last_progress >= .25:
                    event('progress', pending_progress); pending_progress = None; last_progress = time.monotonic()
            proc.wait(timeout=3)
            for thread in threads: thread.join(timeout=.5)
        except Exception as exc:
            log(str(exc)); status = 'failed'
        finally:
            if proc is not None:
                _signal(proc, job, True)
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: pass
            if job: job.close()
            with _lock: _registry.pop(call_id, None)
    if pending_progress: event('progress', pending_progress)
    ok = sum(i['status'] == 'ok' for i in items.values())
    failed = sum(i['status'] == 'failed' for i in items.values())
    if not final: final = {'summary': f'{status.replace("_", " ").capitalize()} · {ok} of {len(items)} items ok'}
    event('result', dict(final, status=status, exit_code=proc.returncode if proc else None, items_total=len(items), items_ok=ok, items_failed=failed, items_snapshot=list(items.values())))
    return json.dumps({'status': status, 'exit_code': proc.returncode if proc else None, 'items': [{k:v for k,v in i.items() if k in ('key','status','detail')} for i in items.values()], 'result': {'summary': final['summary']}, 'log_tail': list(log_tail)}, ensure_ascii=False)


OPTIONS_TIMEOUT = 15
MAX_OPTIONS = 500


def list_param_options(profile, param, values=None, *, timeout=OPTIONS_TIMEOUT):
    """Ask the entry script for one parameter's choices (`--options <param>`).

    Read-only by contract and short-lived: no shell, bounded output and time,
    process tree killed afterwards. Only the parameter's declared
    `options_depends` values are passed. Returns {options, error}.
    """
    from agent_profiles import validate_params
    spec = profile.params.get(param)
    if profile.errors or not profile.entry or not spec or spec.get('options_from') != 'script':
        return {'options': [], 'error': 'This parameter has no script-provided choices'}
    entry = (profile.path / profile.entry).resolve()
    if not entry.is_file() or not entry.is_relative_to(profile.path.resolve()):
        return {'options': [], 'error': 'Agent entry escaped its folder or is missing'}
    depends = spec.get('options_depends', [])
    subset = {k: v for k, v in (values or {}).items() if k in depends}
    checked, errors = validate_params(profile, subset)
    bad = {e.split(':', 1)[0] for e in errors}
    checked = {k: v for k, v in checked.items() if k in depends and k not in bad}
    found, logs = None, deque(maxlen=5)
    with tempfile.TemporaryDirectory(prefix='mh-options-') as scratch:
        param_file = Path(scratch) / 'params.json'; param_file.write_text(json.dumps(checked), encoding='utf-8')
        env = os.environ.copy()
        env.update(MYHARNESS_AGENT='1', MYHARNESS_AGENT_NAME=profile.name, MYHARNESS_AGENT_DIR=str(profile.path), PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
        from agent_profiles import helper_lib_dir
        env['PYTHONPATH'] = str(helper_lib_dir()) + os.pathsep + env.get('PYTHONPATH', '')
        argv = [profile.python or __import__('sys').executable, str(entry), '--params', str(param_file), '--options', param]
        proc = job = None
        try:
            proc = subprocess.Popen(argv, cwd=scratch, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}))
            job = _WindowsJob(proc)
            try: out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _signal(proc, job, True); proc.communicate(timeout=3)
                return {'options': [], 'error': f'Listing choices took longer than {timeout} s'}
            for line in out[:1024 * 1024].decode('utf-8', 'replace').splitlines():
                try: data = json.loads(line)
                except ValueError: continue
                if isinstance(data, dict) and data.get('wj') == 'options' and data.get('param') == param and isinstance(data.get('options'), list): found = data['options']
                elif isinstance(data, dict) and data.get('wj') == 'log' and isinstance(data.get('text'), str): logs.append(data['text'])
            logs.extend(l for l in err.decode('utf-8', 'replace').splitlines()[-3:] if l.strip())
        except Exception as exc:
            return {'options': [], 'error': str(exc)}
        finally:
            if proc is not None:
                _signal(proc, job, True)
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: pass
            if job: job.close()
    if found is None:
        return {'options': [], 'error': (logs[-1] if logs else 'The script did not list any choices')[:500]}
    options, seen = [], set()
    for o in found[:MAX_OPTIONS]:
        value, label = (o, o) if isinstance(o, str) else ((o.get('value'), o.get('label') or o.get('value')) if isinstance(o, dict) else (None, None))
        if isinstance(value, str) and isinstance(label, str) and value and value not in seen and len(value) <= 500:
            seen.add(value); options.append({'value': value, 'label': label[:200]})
    return {'options': options, 'error': ''}
