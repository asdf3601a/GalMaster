# GalMaster

視窗擷取 + OCR / VLM + LLM 翻譯小工具，給 galgame / 視覺小說玩家使用。

## 功能

- **框選 OCR 區域** + **全域熱鍵**（預設 `Ctrl+Shift+T`）擷取並翻譯  
- **綁定遊戲視窗**（區域隨視窗；**Windows 視窗擷取** Automatic：WGC → PrintWindow/BitBlt → 螢幕 fallback，對齊 OBS 視窗擷取）  
- **未綁定／全螢幕區域**仍用螢幕擷取（mss）  
- **自動監控**：頂欄 **開始／停止監控**；穩定時間／間隔／觸發冷卻／變化閾值  
- **處理緩衝**：OCR/LLM 忙碌時可排隊數張截圖（預設 3；可調）  
- **主視窗依管線分組**：偵測 → 擷取 → 處理 → 辨識 → 翻譯 → Overlay / OBS → 應用  
- **半透明置頂 Overlay**（可拖曳移動、**拖邊緣調整大小**、透明度／樣式、滑鼠穿透；結束請在主程式）  
- **三模式管線**：本地 OCR → 翻譯 ／ VLM OCR → 翻譯 ／ VLM 直翻  
- **雙獨立 API 端點**：翻譯 API 與 VLM API（provider / key / URL / model / 逾時／進階參數各自一套）  
- **API 逾時與 soft-fail**（預設單次約 30 秒；失敗不永久卡住 Process）  
- **OpenAI / Anthropic Compatible**（未勾選的採樣參數不送出）  
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

# 格式化 / 靜態檢查（Ruff）
uv run ruff format app tests
uv run ruff check app tests
```

開發與 agent 約定見 [AGENTS.md](AGENTS.md)。

## 管線模式

主視窗 **處理** 區塊選模式；**辨識／翻譯** 表單會依模式只顯示會用到的設定。

| 模式 | 辨識 | 翻譯 | 需要的設定 |
|------|------|------|------------|
| **OCR → 翻譯** | 本地 OCR 引擎 | 文字 LLM（可選） | 本地引擎；翻譯 API Key 可留空＝只辨識 |
| **VLM OCR → 翻譯** | VLM 只出原文 | 文字 LLM（可選） | **VLM API**；翻譯 API Key 可留空＝只辨識 |
| **VLM 直翻** | VLM 一刀原文 + 譯文 | （不使用翻譯 API） | **僅 VLM API**（需 vision 模型） |

```
Detect → Capture → Process → Present
                      ├─ ocr:      本地 OCR  → [翻譯 API?]
                      ├─ vlm_ocr:  VLM 辨識  → [翻譯 API?]
                      └─ vlm:      VLM 直翻（結束）
```

## LLM / VLM API（OpenAI / Anthropic 相容）

兩套端點 **互相獨立**（可不同 provider、key、base URL、model、逾時）：

| 端點 | Config 前綴 | 用途 |
|------|-------------|------|
| **翻譯 API** | 無前綴（`api_key`、`model`、`llm_timeout_s`…） | `ocr` / `vlm_ocr` 的文字翻譯 |
| **VLM API** | `vlm_*`（`vlm_api_key`、`vlm_model`、`vlm_timeout_s`…） | `vlm` 直翻、`vlm_ocr` 第一段辨識 |

每個端點都有 **服務預設** 選單（內含協議，不必另選）：

| 服務預設 | 協議 | 端點 |
|----------|------|------|
| SpaceXAI / xAI、OpenAI 官方、OpenAI 相容 | OpenAI Compatible | `POST {base}/chat/completions` |
| Anthropic 官方、Anthropic 相容 | Anthropic Compatible | `POST {base}/v1/messages` |

切換服務預設會帶入該端點的 Base URL / Model；仍可手動改 Key、URL、Model。  
單次請求 **逾時** 可設（預設 30 秒，約 5–120）；逾時或 API 錯誤會 soft-fail，並可顯示已辨識原文（若有）。

### 環境變數

複製 `.env.example` 為 `.env`，或在 UI 直接填。設定存到**工具目錄**的 `config.json`（與 `start.bat` 同層；已列入 `.gitignore`）。  
若尚無本機設定，會嘗試從舊版 `%APPDATA%\GalMaster\config.json` 遷移一次。  
**首次**從無 `vlm_*` 的舊檔升級時，會一次性把翻譯端點複製為 VLM 預設；之後兩套分開。

```env
# ----- 翻譯 API（LLM Translate）-----
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

# ----- VLM API（視覺／辨識；與翻譯獨立）-----
# VLM_API_KEY=...
# VLM_BASE_URL=https://api.x.ai/v1
# VLM_MODEL=grok-4-1-fast-non-reasoning
# VLM_PROTOCOL=openai
# VLM_PROVIDER=xai
```

| 行為 | 說明 |
|------|------|
| 翻譯 Key 空 | `ocr` / `vlm_ocr` 只做辨識（ocr_only） |
| VLM Key 空 | `vlm` / `vlm_ocr` 無法跑（需填 VLM） |
| 只設 `XAI_API_KEY` 且兩邊 UI Key 都空 | 兩邊 env 都可能被填成同一把 key（方便）；若要嚴格分開請設 `LLM_API_KEY` / `VLM_API_KEY` 或只在 UI 分填 |

### 進階參數

temperature / top_p / top_k / frequency_penalty / presence_penalty / reasoning_effort / seed 等可在 **各端點** UI 勾選後送出。  
**未勾選的參數不會出現在 API JSON**（避免閘道拒收）。  

> **行為變更**：先前固定送 `temperature=0.2`；現在預設**不送** temperature（由供應商預設，常見約 1.0）。若需要較穩定的對白翻譯，請在「進階參數」勾選 temperature 並設為 `0.2`。

### 自動監控與緩衝

| 設定 | 說明 |
|------|------|
| **穩定時間** | 變化後需安靜 N ms；**0** = 變化即觸發 |
| **間隔** | 輪詢畫面間隔；0 → 預設 600 ms（下限約 200） |
| **觸發冷卻** | 兩次自動觸發最小間隔；**0** = 不額外冷卻 |
| **處理緩衝** | Process（OCR/LLM）忙碌時，最多再排隊幾張已截圖；滿則丟最舊。預設 **3** |

手動／熱鍵翻譯（force）不受間隔與冷卻限制，並會清掉等待中的自動 Process 工作。

### OBS 字幕

1. 勾選 **啟用 OBS 伺服器**，設定連接埠（預設 `8765`）→ **套用**  
2. OBS → 來源 → **瀏覽器** → URL：`http://127.0.0.1:8765/`  
3. 原文／譯文顯示、字型、字級、顏色、面板背景等在主視窗 **OBS 字幕** 群組調整；樣式經 SSE／輪詢即時更新，**不必重新整理** Browser Source  

僅綁定本機 loopback。

## 使用流程

1. 啟動 `uv run galmaster`  
2. （建議）在 **偵測** 綁定遊戲視窗 →「重新整理」  
3. 按 **框選 OCR 區域**，拖曳對話框範圍  
4. 在 **處理** 選管線模式與來源／目標語言  
5. 依模式填 **辨識**（本地引擎或 VLM）與 **翻譯 API**（若需要）→ 頂欄 **套用** 或 **儲存**  
6. 按 **立即翻譯** 或熱鍵 `Ctrl+Shift+T`；**擷取** 區預覽可對框  
7. 頂欄 **開始監控**／**停止監控**  
8. Overlay 可拖曳；「穿透」讓滑鼠點穿到遊戲  
9. 結束請用主視窗 **結束程式** 或系統匣「結束」（會一併關掉 Overlay）  

頂欄固定：**狀態**、**開始／停止監控**、**套用／儲存／取消**。  
**套用**＝立刻生效不寫檔；**儲存**＝套用並寫入 `config.json`；**取消**＝還原未套用變更。  
下拉選單（Dropdown）**不會**被滑鼠滾輪改值；數值框需 focus 後才可用滾輪。

## OCR（本地引擎）

僅 **OCR → 翻譯** 模式使用（預設 **OneOCR**）。`vlm` / `vlm_ocr` 不使用本地引擎。

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
  session/             # Capture 階段狀態（Idle / Capturing）
  config.py            # 雙端點 + 遷移
  pipeline.py          # OCR | VLM | VLM-OCR→Translate（Process）
  pipeline_queue.py    # Process 有界 FIFO
  capture/             # 視窗、截圖、監控（Detect）
  ocr/                 # OneOCR / Manga / Rapid / Paddle
  translate/           # LLM/VLM client + 快取 + sampling
  obs/                 # Browser Source 字幕伺服器
  i18n/                # en / zh-Hant
  ui/                  # 主視窗、Overlay、框選
  hotkeys/             # 全域熱鍵
docs/
  architecture.md      # 模組邊界與管線
  state-machine.md     # 狀態與事件
```

執行緒模型（摘要）：**Detect**（監控 daemon）→ **Capture**（單次截圖 daemon）→ **Process**（單一 QThread worker + 有界佇列）→ **Present**（UI 執行緒）。詳見 [docs/architecture.md](docs/architecture.md)。

## 授權

個人／專案用途自用即可。
