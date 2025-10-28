from __future__ import annotations
import re
from typing import Literal
from app.state_store import get_pref, set_pref

ColorScheme = Literal["tw", "us"]  # tw=多紅空綠, us=多綠空紅

_TW_PAT = re.compile(r"(台\s*股|臺\s*股|🇹🇼|TW|ＴＷ)", re.I)
_US_PAT = re.compile(r"(美\s*股|🇺🇸|US|ＵＳ)", re.I)

def resolve_scheme(text: str) -> ColorScheme | None:
    t = (text or "").replace("\u3000", " ").strip()  # 全形空白→半形
    if _TW_PAT.search(t):
        return "tw"
    if _US_PAT.search(t):
        return "us"
    return None

def set_color_scheme(scheme: ColorScheme) -> str:
    set_pref("color_scheme", scheme)
    return "已切換為台股配色（多紅／空綠）" if scheme == "tw" else "已切換為美股配色（多綠／空紅）"

def current_scheme() -> ColorScheme:
    return get_pref("color_scheme", "tw")
