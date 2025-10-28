# app/services/watches.py
import time
from .state_store import list_watches, upsert_watch, stop_watch
__all__ = ["list_watches","extend_1h","stop"]

def extend_1h(symbol: str):
    until = int(time.time()) + 3600
    upsert_watch(symbol.upper(), until)

def stop(symbol: str):
    stop_watch(symbol.upper())
