# GalMaster

視窗擷取 + OCR + LLM 翻譯小工具，給 galgame / 視覺小說玩家使用。

## 功能

- **框選 OCR 區域** + **全域熱鍵**（預設 `Ctrl+Shift+T`）擷取並翻譯  
- **綁定遊戲視窗**（區域隨視窗；擷取優先取視窗內容，少截到 Overlay）  
- **自動監控**：頂欄 **開始／停止監控**；可設穩定時間（0 = 變化即辨識）  
- **主視窗工作狀態**：狀態與套用／儲存／取消固定在頂部  
- **半透明置頂 Overlay**（可拖曳、調透明度／字級、滑鼠穿透；結束請在主程式）  
- **OCR**：OneOCR（預設）／Manga OCR／RapidOCR／PaddleOCR  
- **多語系** + **OpenAI / Anthropic Compatible LLM** 翻譯（可選）  

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

**LLM 為可選**：API Key 留空時只做 OCR，不呼叫翻譯。

## 使用流程

1. 啟動 `uv run galmaster`  
2. （建議）從下拉選單綁定遊戲視窗 →「重新整理」  
3. 按 **框選 OCR 區域**，拖曳對話框範圍  
4. 設定來源／目標語言與 API Key → 頂欄 **套用** 或 **儲存**  
5. 按 **立即翻譯** 或熱鍵 `Ctrl+Shift+T`  
6. 頂欄 **開始監控**／**停止監控**；擷取區可設穩定時間／間隔／變化閾值（穩定時間 **0** = 變化即辨識）  
7. Overlay 可拖曳；「穿透」讓滑鼠點穿到遊戲  
8. 結束請用主視窗 **結束程式** 或系統匣「結束」（會一併關掉 Overlay）  

頂欄固定：**狀態**、**開始／停止監控**、**套用／儲存／取消**。  
**套用**＝立刻生效不寫檔；**儲存**＝套用並寫入 `config.json`；**取消**＝還原未套用變更。  
下拉選單（Dropdown）**不會**被滑鼠滾輪改值；數值框需 focus 後才可用滾輪。

## OCR

可選引擎（預設 **OneOCR**）：

| 引擎 | 說明 |
|------|------|
| **OneOCR** | 剪取工具 `oneocr.dll` 離線模型（推薦） |
| **Manga OCR** | 日文對白／遊戲字體友善 |
| **RapidOCR** | ONNX 輕量多語 |
| **PaddleOCR** | PP-OCR（較重；可選 `uv sync --extra paddle-native`） |

每次辨識會把截圖存成工具目錄的 `last_capture.png`，失敗時可打開確認是否框到字。  
若全黑：多半是框選區域沒有文字、或視窗內容未正確擷取——請重新框選並確認綁定視窗仍有效。

首次 OneOCR 會從已安裝的剪取工具複製模型到 `tools/oneocr`。

## 專案結構

```
app/
  main.py              # 進入點
  app_controller.py    # 串起 UI / 熱鍵 / 管線
  config.py
  pipeline.py          # 截圖 → OCR → LLM
  capture/             # 視窗、截圖、監控（含 stable frame）
  ocr/                 # OneOCR / Manga / Rapid / Paddle
  translate/           # LLM + 快取
  ui/                  # 主視窗、Overlay、框選
  hotkeys/             # 全域熱鍵
```

## 授權

個人／專案用途自用即可。
