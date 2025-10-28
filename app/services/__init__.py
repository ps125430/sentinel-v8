# app/services/__init__.py 〔覆蓋版〕
# 將 services 當成乾淨的套件出口，避免循環或無效名稱

from . import prefs
from . import watches
# version_diff 模組若有放在 app/services/ 底下，就一起匯出
try:
    from . import version_diff  # 可選
except Exception:
    version_diff = None

__all__ = ["prefs", "watches", "version_diff"]
