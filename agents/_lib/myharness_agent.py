"""Portable Python 3.8+ routine-agent protocol and cooperative stop helpers."""
import argparse
import json
import os
import signal
import threading

_stop = threading.Event()
_callbacks = []
_args = None


def _signal_stop(signum, frame):
    if _stop.is_set(): return
    _stop.set()
    for callback in list(_callbacks):
        try: callback()
        except Exception as exc: log(str(exc), 'error')


if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGBREAK if os.name == 'nt' else signal.SIGTERM, _signal_stop)


def load_params(defaults=None):
    global _args
    parser = argparse.ArgumentParser()
    parser.add_argument('--params')
    parser.add_argument('--set', action='append', default=[])
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--options', metavar='PARAM', help='list choices for one parameter and exit')
    _args = parser.parse_args()
    values = dict(defaults or {})
    if _args.params:
        with open(_args.params, encoding='utf-8') as stream: values.update(json.load(stream))
    for assignment in _args.set:
        key, sep, value = assignment.partition('=')
        if not sep: parser.error('--set requires key=value')
        try: value = json.loads(value)
        except ValueError: pass
        values[key] = value
    return values


def dry_run(): return bool(_args and _args.dry_run)
def options_request():
    """Parameter name when the form asks for its choices (`--options`), else None."""
    return _args.options if _args else None
def options(values, param=None):
    """Report choices for the requested parameter: strings or {value, label}. Keep it fast and read-only."""
    param = param or options_request()
    if os.environ.get('MYHARNESS_AGENT') == '1':
        print(json.dumps({'wj': 'options', 'param': param, 'options': list(values)}, ensure_ascii=False), flush=True)
    else:
        for v in values: print(v.get('label', v.get('value')) if isinstance(v, dict) else v, flush=True)
def stopping(): return _stop.is_set()
def on_stop(callback):
    _callbacks.append(callback)
    if stopping(): callback()


def _emit(wj, **data):
    if os.environ.get('MYHARNESS_AGENT') == '1':
        print(json.dumps(dict(wj=wj, **data), ensure_ascii=False), flush=True)
    elif wj == 'progress':
        print('[{}/{}] {}'.format(data['done'], data['total'] or '?', data['label']), flush=True)
    elif wj == 'item':
        print('{} {}  {}'.format({'ok': '✓', 'failed': '✗', 'skipped': '–'}.get(data['status'], '…'), data['label'] or data['key'], data['detail']), flush=True)
    else:
        print(data.get('summary', data.get('text', '')), flush=True)
        if data.get('table'):
            print(' | '.join(data['table']['columns']), flush=True)
            for row in data['table']['rows']: print(' | '.join(map(str, row)), flush=True)


def progress(done, total=None, label=''): _emit('progress', done=done, total=total, label=label)
def item(key, status, label='', detail='', data=None): _emit('item', key=str(key), status=status, label=label, detail=detail, data=data or {})
def result(summary, table=None, markdown=None): _emit('result', summary=summary, table=table, markdown=markdown)
def log(text, level='info'): _emit('log', text=text, level=level)


def allowed_path(path):
    """Check expanded output paths when running inside MyHarness; manual runs use caller permissions."""
    from pathlib import Path
    resolved = Path(path).expanduser().resolve()
    roots = json.loads(os.environ.get('MYHARNESS_ALLOWED_PATHS', 'null'))
    if roots is not None:
        def inside(root):
            try: return os.path.normcase(os.path.commonpath([str(resolved), str(Path(root).resolve())])) == os.path.normcase(str(Path(root).resolve()))
            except ValueError: return False
        if not any(inside(root) for root in roots): raise ValueError('Output path is outside allowed roots: ' + str(resolved))
    return resolved
