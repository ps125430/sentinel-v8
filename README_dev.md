# 🛰️ Crypto Flow Sentinel v8 專案開發筆記（接軌文件）

> 開新視窗用這份貼上開頭，我就能立即對接。  
> 若要更新內容，只需在此補上修改後的模組或狀態。

---

## 🔗 系統基本資料
- Render 部署網址：`https://sentinel-v8.onrender.com`
- GitHub Repo：`ps125430/sentinel-v8`
- 架構：`FastAPI + APScheduler + LINE Bot`
- Python 版本：3.13  
- Timezone：Asia/Taipei  
- 主檔案：`app/main.py`  
- 主要模組：
  - `trend_integrator.py`（🔥⚡🌙💤 主升浪判斷）
  - `state_store.py`（狀態快照 / 持久化）
  - `services/prefs.py`（顏色偏好管理）
  - `services/watches.py`（監控任務延長/停止）
  - `news_scoring.py`（預留：新聞分數整合）
- LINE webhook 路由：`/line/webhook`
- 排程時間：09:30／12:30／18:00／22:30（四時段推播）

---

## ⚙️ 系統邏輯概述
1. **市場數據來源**：CoinGecko → Binance fallback  
2. **分數計算**：
   - `score_strong = 漲幅×量能`
   - `score_total = 0.6×strong + 0.4×news`（目前 news=0）
3. **相位顯示**：🔥主升浪／⚡接棒／🌙轉弱／💤觀望  
4. **動作決策**：
   - ≥70 且相位🔥⚡ → [多]  
   - ≤30 且相位🌙或跌幅<0 → [空]
5. **顏色機制**：
   - `DEFAULT_COLOR_SCHEME = tw`
   - 台股：多=紅🟥，空=綠🟩  
   - 美股：多=綠🟩，空=紅🟥  
   - LINE 指令：「顏色 台股」／「顏色 美股」切換
6. **符號操作流**：
   - `<幣> 做多／做空`
   - `<幣> +`（延長 1h）
   - `<幣> -`（停止）
   - 無操作自動到期、5 分鐘前提醒。

---

## 💬 LINE 常用指令
| 類型 | 範例 | 功能 |
|------|------|------|
| 強弱分析 | `今日強勢` / `今日弱勢` | 取得市場前 3 名強弱幣種 |
| 手動監控 | `BTC 做多` / `ETH 做空` | 建立 1 小時監控任務 |
| 延長 / 停止 | `BTC +` / `BTC -` | 延長 1 小時或停止任務 |
| 狀態查詢 | `總覽` | 查看目前所有監控 |
| 配色切換 | `顏色 台股` / `顏色 美股` | 切換多空配色偏好 |

---

## 🧩 模組路徑與職責
| 檔案 | 功能 |
|------|------|
| `app/main.py` | 核心主程式（FastAPI、排程、Webhook、決策） |
| `app/trend_integrator.py` | 主升浪相位計算與附註 |
| `app/services/prefs.py` | 顏色偏好設定、get/set 介面 |
| `app/services/watches.py` | 延長、停止監控的內部封裝 |
| `app/state_store.py` | 寫入 /tmp/sentinel-v8.json 的輕量持久化 |
| `app/news_scoring.py` | 預留新聞分數引擎（W_NEWS） |

---

## 🧠 目前版本狀態（2025-10-28）
- ✅ 主升浪模型運作正常（🔥⚡🌙💤）
- ✅ 四時段報表定時推播正常
- ✅ 做多/做空/+/- 指令運作正常
- ⚠️ 顏色指令需改為「模糊比對」版本（支援空白、全形、emoji）
- ⚙️ 持久化優化中：顏色偏好與監控狀態將寫入 `/tmp/sentinel-v8.json`

---

## 🚀 推薦下一步
1. 將顏色設定持久化（prefs → state_store）
2. 啟用 `news_scoring.py`（RSS + Google News 整合）
3. 每日快照寫入 Trend History（供回測使用）
4. 增加「強度異常警報」→ 異常分數自動推播

---

## 🪄 對接指令
開新視窗後，輸入以下兩行即可讓我接軌：
