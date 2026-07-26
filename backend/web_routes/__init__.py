"""APIRouter modules for the MyHarness backend, split from web_app.py.

Route modules access mutable server state (store, manager, bridge, locks)
through the ``web_app`` module at call time so tests can patch it.
"""
