# GalMaster

視窗擷取 + OCR + LLM 翻譯小工具，給 galgame / 視覺小說玩家使用。

## 功能

- **框選 OCR 區域** + **全域熱鍵**（預設 `Ctrl+Shift+T`）擷取並翻譯  
- **綁定遊戲視窗**（區域隨視窗；擷取優先取視窗內容，少截到 Overlay）  
- **自動監控**：頂欄 **開始／停止監控**；穩定時間／間隔／觸發冷卻／變化閾值  
- **主視窗工作狀態**：狀態與套用／儲存／取消固定在頂部  
- **半透明置頂 Overlay**（可拖曳、調透明度／字級、滑鼠穿透；結束請在主程式）  
- **OCR**：OneOCR（預設）／Manga OCR／RapidOCR／PaddleOCR；亦可 **VLM 直翻**（略過 OCR，截圖送多模態 LLM）  
- **OpenAI / Anthropic Compatible LLM** 翻譯（可選）+ **進階採樣參數**（未勾選不送出）  
- **介面語言**：繁體中文／English  
- **OBS Browser Source** 字幕頁（本機 `127.0.0.1`）  
- **擷取預覽**（主視窗即時顯示，不再寫 `last_capture.png`）  

## 環境（uv）

```powershell
# 安裝依賴
uv sync --extra dev

# 執行
uv run galmaster

# 測試
uv run pytest -q
uv run python scripts/smoke_headless.py
```

## LLM API（OpenAI / Anthropic 相容）

主視窗用單一 **服務預設** 選單（內含協議，不必另選）：

| 服務預設 | 協議 | 端點 |
|----------|------|------|
| SpaceXAI / xAI、OpenAI 官方、OpenAI 相容 | OpenAI Compatible | `POST {base}/chat/completions` |
| Anthropic 官方、Anthropic 相容 | Anthropic Compatible | `POST {base}/v1/messages` |

切換服務預設會帶入 Base URL / Model；仍可手動改 Key、URL、Model。  

複製 `.env.example` 為 `.env`，或在 UI 直接填：

```env
# 例：xAI
XAI_API_KEY=...

# 例：OpenAI 相容本機
# LLM_PROTOCOL=openai
# OPENAI_API_KEY=ollama
# LLM_BASE_URL=http://127.0.0.1:11434/v1
# LLM_MODEL=llama3.2

# 例：Anthropic
# LLM_PROTOCOL=anthropic
# ANTHROPIC_API_KEY=sk-ant-...
```

設定存到**工具目錄**的 `config.json`（與 `start.bat` 同層；已列入 `.gitignore`）。  
若尚無本機設定，會嘗試從舊版 `%APPDATA%\GalMaster\config.json` 遷移一次。

**LLM 為可選**：API Key 留空時，OCR 模式只做辨識；**VLM 模式必須有 API Key**。

### 管線模式

| 模式 | 行為 |
|------|------|
| **OCR → 翻譯** | 本地 OCR →（有 Key）文字 LLM |
| **VLM 直翻** | 截圖 → 多模態 LLM（需 vision 模型） |

### LLM 進階參數

temperature / top_p / top_k / frequency_penalty / presence_penalty / reasoning_effort / seed 等可在 UI 勾選後送出。  
**未勾選的參數不會出現在 API JSON**（避免閘道拒收）。  

> **行為變更**：先前固定送 `temperature=0.2`；現在預設**不送** temperature（由供應商預設，常見約 1.0）。若需要較穩定的對白翻譯，請在「進階參數」勾選 temperature 並設為 `0.2`。

### 自動監控節流

| 設定 | 說明 |
|------|------|
| **穩定時間** | 變化後需安靜 N ms；**0** = 變化即觸發 |
| **間隔** | 輪詢畫面間隔；0 → 預設 600 ms（下限約 200） |
| **觸發冷卻** | 兩次自動觸發最小間隔；**0** = 不額外冷卻 |
| 管線忙碌 | 處理中略過新的自動觸發 |

手動／熱鍵翻譯不受間隔與冷卻限制。

### OBS 字幕

1. 勾選 **啟用 OBS 伺服器**，設定連接埠（預設 `8765`）→ **套用**  
2. OBS → 來源 → **瀏覽器** → URL：`http://127.0.0.1:8765/`  
3. 原文／譯文顯示、字型、字級、顏色、面板背景等在主視窗 **OBS 字幕** 群組調整；樣式經 SSE／輪詢即時更新，**不必重新整理** Browser Source  

僅綁定本機 loopback。

## 使用流程

1. 啟動 `uv run galmaster`  
2. （建議）從下拉選單綁定遊戲視窗 →「重新整理」  
3. 按 **框選 OCR 區域**，拖曳對話框範圍  
4. 設定管線模式、來源／目標語言與 API Key → 頂欄 **套用** 或 **儲存**  
5. 按 **立即翻譯** 或熱鍵 `Ctrl+Shift+T`；主視窗 **擷取預覽** 可對框  
6. 頂欄 **開始監控**／**停止監控**  
7. Overlay 可拖曳；「穿透」讓滑鼠點穿到遊戲  
8. 結束請用主視窗 **結束程式** 或系統匣「結束」（會一併關掉 Overlay）  

頂欄固定：**狀態**、**開始／停止監控**、**套用／儲存／取消**。  
**套用**＝立刻生效不寫檔；**儲存**＝套用並寫入 `config.json`；**取消**＝還原未套用變更。  
下拉選單（Dropdown）**不會**被滑鼠滾輪改值；數值框需 focus 後才可用滾輪。

## OCR

可選引擎（預設 **OneOCR**；VLM 模式不使用）：

| 引擎 | 說明 |
|------|------|
| **OneOCR** | 剪取工具 `oneocr.dll` 離線模型（推薦） |
| **Manga OCR** | 日文對白／遊戲字體友善 |
| **RapidOCR** | ONNX 輕量多語 |
| **PaddleOCR** | PP-OCR（較重；可選 `uv sync --extra paddle-native`） |

擷取結果顯示於主視窗預覽；若全黑，請重新框選並確認綁定視窗仍有效。

首次 OneOCR 會從已安裝的剪取工具複製模型到 `tools/oneocr`。

## 專案結構

```
app/
  main.py              # 進入點
  app_controller.py    # 串起 UI / 熱鍵 / 管線
  config.py
  pipeline.py          # 截圖 → OCR|VLM → LLM
  capture/             # 視窗、截圖、監控
  ocr/                 # OneOCR / Manga / Rapid / Paddle
  translate/           # LLM + 快取 + sampling
  obs/                 # Browser Source 字幕伺服器
  i18n/                # en / zh-Hant
  ui/                  # 主視窗、Overlay、框選
  hotkeys/             # 全域熱鍵
```

## 授權

個人／專案用途自用即可。
