"""Bounded, content-free diagnostics for startup and session loading."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import time

_lock = threading.Lock()
_logger = logging.getLogger("myharness.performance")
_logger.propagate = False

def configure(directory=None):
    with _lock:
        if _logger.handlers:
            return
        try:
            folder = Path(directory) if directory else Path(__file__).resolve().parent.parent / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(folder / "performance.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            _logger.addHandler(handler)
            _logger.setLevel(logging.INFO)
        except OSError:
            pass

def record(phase, started, **measurements):
    # Callers pass only counts, durations, sizes and outcome codes, never content.
    try:
        _logger.info(json.dumps({"phase": phase, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2), **measurements}))
    except Exception:
        pass
