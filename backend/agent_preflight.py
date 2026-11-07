"""Bounded checks performed with the entry script's interpreter."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

# Checks start interpreters and probe URLs, and the catalog is listed on every
# session switch and window focus. Reuse results briefly; run starts pass fresh.
CACHE_SECONDS = 30
_cache = {}
_cache_lock = threading.Lock()


def _cache_key(profile):
    return (str(profile.path), profile.python, json.dumps(list(profile.requires), sort_keys=True, default=str), tuple(profile.platform))


def preflight(profile, fresh=False):
    key = _cache_key(profile)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not fresh and now - hit[0] < CACHE_SECONDS:
            return [dict(c) for c in hit[1]]
    checks = _run_checks(profile)
    with _cache_lock:
        if len(_cache) > 256: _cache.clear()
        _cache[key] = (time.monotonic(), checks)
    return [dict(c) for c in checks]


def _run_checks(profile):
    checks=[]
    current={'darwin':'macos','win32':'windows'}.get(sys.platform,'linux')
    if profile.platform:
        checks.append({'check':{'platform':list(profile.platform)},'ok':current in profile.platform,'message':f'Platform: {current}; supported: {", ".join(profile.platform)}'})
    for check in profile.requires:
        ok=False; message='Invalid preflight check'
        try:
            if not isinstance(check,dict): raise ValueError('requires entries must be maps')
            if 'python_module' in check:
                name=check['python_module']
                code='import importlib,sys; importlib.import_module(sys.argv[1])'
                response=subprocess.run([profile.python or sys.executable,'-c',code,name],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=3,text=True)
                ok=response.returncode==0;message=f'Python module {name}: '+('available' if ok else 'not importable by the agent interpreter')
            elif 'env' in check:
                ok=bool(os.environ.get(check['env']));message=f"Environment variable {check['env']}: "+('set' if ok else 'missing')
            elif 'file' in check:
                value=os.path.expandvars(check['file'])
                if '$' in value or '%' in value:
                    # The env check reports the unset variable; do not echo an unexpanded path.
                    ok=False;message=f"File {check['file']}: path depends on an unset environment variable"
                else:
                    ok=Path(value).is_file();message=f'File {value}: '+('available' if ok else 'missing')
            elif 'url' in check:
                with urlopen(check['url'],timeout=3) as response: ok=200<=response.status<400
                message=f"URL {check['url']}: "+('reachable' if ok else 'unavailable')
            elif 'python' in check:
                code='import json,platform,sys;print(json.dumps(["%s.%s"%sys.version_info[:2],platform.machine(),64 if sys.maxsize>2**32 else 32]))'
                response=subprocess.run([profile.python or sys.executable,'-c',code],capture_output=True,text=True,timeout=3,check=True)
                version,arch,bits=json.loads(response.stdout)
                requested=check.get('arch','')
                arch_ok=not requested or (requested=='x64' and arch.lower() in ('x86_64','amd64') and bits==64) or requested.lower()==arch.lower()
                ok=version==str(check['python']) and arch_ok;message=f'Interpreter: Python {version} {arch}; requires {check["python"]} {requested}'
            checks.append({'check':check,'ok':ok,'message':message})
        except Exception as exc: checks.append({'check':check,'ok':False,'message':str(exc)[:500]})
    return checks
