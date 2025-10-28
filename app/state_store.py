# app/state_store.py
import json, os, time, threading

PATH = "/tmp/sentinel-v8.json"
DEFAULT = {"color_pref": {}, "watches": [], "snapshots": []}
_LOCK = threading.Lock()

def _load():
    if not os.path.exists(PATH):
        return DEFAULT.copy()
    try:
        with open(PATH, "r") as f:
            return json.load(f)
    except Exception:
        return DEFAULT.copy()

def _save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, PATH)

def get_color_pref(room_id: str, fallback="tw"):
    with _LOCK:
        d = _load()
        return d["color_pref"].get(room_id, fallback)

def set_color_pref(room_id: str, scheme: str):
    with _LOCK:
        d = _load()
        d["color_pref"][room_id] = scheme
        _save(d)

def list_watches():
    with _LOCK:
        return _load()["watches"]

def upsert_watch(symbol: str, until_ts: int):
    with _LOCK:
        d = _load()
        d["watches"] = [w for w in d["watches"] if w["symbol"] != symbol]
        d["watches"].append({"symbol": symbol, "until": until_ts})
        _save(d)

def stop_watch(symbol: str):
    with _LOCK:
        d = _load()
        d["watches"] = [w for w in d["watches"] if w["symbol"] != symbol]
        _save(d)

def add_snapshot(payload: dict):
    with _LOCK:
        d = _load()
        payload = dict(payload)
        payload["ts"] = int(time.time())
        d["snapshots"].append(payload)
        d["snapshots"] = d["snapshots"][-500:]  # 避免太肥
        _save(d)
