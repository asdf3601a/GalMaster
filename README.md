# GalMaster

視窗擷取 + OCR + LLM 翻譯小工具，給 galgame / 視覺小說玩家使用。

## 功能

- **框選 OCR 區域** + **全域熱鍵**（預設 `Ctrl+Shift+T`）擷取並翻譯  
- **綁定遊戲視窗**（區域隨視窗；擷取優先取視窗內容，少截到 Overlay）  
- **自動監控**：畫面變化後等待 **stable frame** 再 OCR → 翻譯  
- **主視窗工作狀態**：截圖 / OCR / 翻譯 / 等待穩定 等即時顯示  
- **半透明置頂 Overlay**（可拖曳、調透明度／字級、滑鼠穿透；結束請在主程式）  
- **OCR 自動模式（預設）**：manga-ocr（日文強）+ PP-OCR 偵測；可切換 manga / Paddle / Rapid  
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
4. 設定來源／目標語言與 API Key → 按 **套用** 或 **儲存**（不會自動寫入）  
5. 按 **立即翻譯** 或熱鍵 `Ctrl+Shift+T`  
6. 可勾選 **自動監控**；可選 **僅在畫面穩定後才開始辨識**  
7. Overlay 可拖曳；「穿透」讓滑鼠點穿到遊戲  
8. 結束請用主視窗 **結束程式** 或系統匣「結束」（會一併關掉 Overlay）  
   Overlay 只有「隱藏／穿透」，沒有結束按鈕  

設定按鈕：**套用**＝立刻生效不寫檔；**儲存**＝套用並寫入 `config.json`；**取消**＝還原未套用變更。  
滾輪只捲動主視窗，不會改下拉／數值（需先點選後才可用滾輪）。

## OCR

預設 **自動（Hybrid）**：
- **manga-ocr** 辨識日文對白（已測：`こんにちは、世界。` 等可正確讀出）
- **PP-OCR** 做文字偵測／多行備援
- 每次辨識會把截圖存成工具目錄的 `last_capture.png`，失敗時可打開確認是否框到字

若 OCR 失敗且 `last_capture.png` 是全黑：多半是框選區域沒有文字、或遊戲/瀏覽器視窗內容未正確擷取——請重新框選對話框並確認綁定視窗仍有效。

主視窗 OCR 可切換：自動 / manga-ocr / PaddleOCR / RapidOCR。

## 專案結構

```
app/
  main.py              # 進入點
  app_controller.py    # 串起 UI / 熱鍵 / 管線
  config.py
  pipeline.py          # 截圖 → OCR → LLM
  capture/             # 視窗、截圖、監控（含 stable frame）
  ocr/                 # PaddleOCR / RapidOCR / 可選 manga-ocr
  translate/           # LLM + 快取
  ui/                  # 主視窗、Overlay、框選
  hotkeys/             # 全域熱鍵
```

## 可選：manga-ocr

日文豎排／遊戲字體較難時可試：

```powershell
uv sync --extra manga
```

主視窗 OCR 選「manga-ocr」。

## 授權

個人／專案用途自用即可。
