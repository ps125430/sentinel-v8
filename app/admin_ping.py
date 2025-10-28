# app/admin_ping.py 〔新檔・一鍵貼上〕
from __future__ import annotations
from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin-ping"])

@router.get("/ping-services")
def ping_services():
    ok = {}
    errors = {}

    # 檢查 app.services.prefs
    try:
        from app.services import prefs
        ok["prefs"] = True
    except Exception as e:
        ok["prefs"] = False
        errors["prefs"] = str(e)

    # 檢查 app.services.watches
    try:
        from app.services import watches
        ok["watches"] = True
    except Exception as e:
        ok["watches"] = False
        errors["watches"] = str(e)

    # 檢查 app.services.version_diff（允許不存在）
    try:
        from app.services import version_diff  # type: ignore
        ok["version_diff"] = version_diff is not None
        if version_diff is None:
            errors["version_diff"] = "module not present (optional)"
    except Exception as e:
        ok["version_diff"] = False
        errors["version_diff"] = str(e)

    return {"ok": ok, "errors": errors}
