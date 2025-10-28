from __future__ import annotations
import time, re
from app.state_store import set_watch, del_watch, list_watches

_PLUS_PAT = re.compile(r"^\s*([A-Za-z0-9_\-\.]+)\s*\+\s*$")
_MINUS_PAT = re.compile(r"^\s*([A-Za-z0-9_\-\.]+)\s*\-\s*$")

DEFAULT_HOURS = 1

def parse_plus(text: str) -> str | None:
    m = _PLUS_PAT.match(text)
    return m.group(1) if m else None

def parse_minus(text: str) -> str | None:
    m = _MINUS_PAT.match(text)
    return m.group(1) if m else None

def extend(symbol: str, hours: int = DEFAULT_HOURS) -> str:
    now = int(time.time())
    watches = list_watches()
    sym = symbol.upper()
    old_until = watches.get(sym, {}).get("until", now)
    base = max(now, old_until)
    new_until = base + hours * 3600
    set_watch(sym, new_until)
    return f"{sym} 監控已延長 {hours} 小時（至 {time.strftime('%Y-%m-%d %H:%M', time.localtime(new_until))}）"

def stop(symbol: str) -> str:
    del_watch(symbol)
    return f"{symbol.upper()} 監控已停止"

def summarize(now_ts: int | None = None) -> str:
    now = int(now_ts or time.time())
    ws = list_watches()
    if not ws:
        return "（目前無監控標的）"
    parts = []
    for sym, v in ws.items():
        until = v.get("until", 0)
        if until:
            parts.append(f"{sym}: 監控至 {time.strftime('%m/%d %H:%M', time.localtime(until))}（剩 {max(0, (until-now)//60)} 分）")
        else:
            parts.append(f"{sym}: 監控中")
    return "、".join(parts)
