# AGENTS.md — GalMaster

給人類與 coding agent 的工作指引。產品說明見 [README.md](README.md)；架構細節見 [docs/architecture.md](docs/architecture.md)、[docs/state-machine.md](docs/state-machine.md)。

## 專案簡介

GalMaster 是 **Windows** 桌面工具：擷取遊戲視窗／螢幕區域 → 本地 OCR 或 VLM → 可選 LLM 翻譯 → 主視窗、半透明 Overlay、OBS Browser Source 呈現。目標使用情境是 galgame / 視覺小說。

## 技術棧

| 項目 | 選擇 |
|------|------|
| Python | ≥ 3.12（見 `.python-version`） |
| 套件管理 | **uv**（`pyproject.toml` + `uv.lock`） |
| 格式化 / Lint | **Ruff**（`[tool.ruff*]` in `pyproject.toml`） |
| UI | PySide6 |
| 打包 | hatchling；package 入口 `app`，CLI `galmaster` |

依賴變更請改 `pyproject.toml` 後執行 `uv lock` / `uv sync`，不要手改 lock 語意。

## 常用指令

```powershell
# 安裝（含 pytest、ruff）
uv sync --extra dev

# 執行
uv run galmaster

# 測試
uv run pytest -q

# 格式化 / 靜態檢查（權威工具：Ruff）
uv run ruff format app tests
uv run ruff format --check app tests
uv run ruff check app tests
uv run ruff check app tests --fix
```

提交前邏輯變更至少跑 `uv run pytest -q`；格式與 lint 應通過 `ruff format --check` 與 `ruff check`。

## 目錄與模組邊界

```
app/
  main.py              # QApplication 入口
  app_controller.py    # 串 UI、熱鍵、monitor、capture、pipeline、present
  config.py            # AppConfig 讀寫（專案根 config.json）
  pipeline.py          # Process：OCR / VLM / 翻譯（QThread）
  pipeline_queue.py    # Process 等待佇列
  session/             # Capture 階段狀態
  capture/             # 視窗／螢幕擷取、DPI、WGC
  ocr/                 # 本地 OCR 引擎
  translate/           # LLM、快取、provider 預設
  obs/                 # 本機字幕 HTTP + SSE
  ui/                  # MainWindow、Overlay、框選
  hotkeys/             # 全域熱鍵
  i18n/                # zh-Hant / en
tests/                 # pytest
docs/                  # 架構與狀態機
```

**依賴規則（目標）：**

| 模組 | 可依賴 | 不應依賴 |
|------|--------|----------|
| `config` | stdlib | UI、OCR、pipeline |
| `capture` | Win32 / mss / WGC、config DTO | UI、LLM、pipeline |
| `ocr` / `translate` | 影像／文字 API | UI、controller |
| `pipeline` | ocr、translate、**已擷取影像** | UI；**不可呼叫 capture** |
| `session` | 純狀態 | Qt widgets（必要型別除外） |
| `app_controller` | services + signals | 表單欄位細節 |
| `ui` | config、i18n、signals | capture 執行緒／OCR worker |

新增功能時維持分層：Detect → Capture → Process → Present，不要把擷取塞進 pipeline worker。

## 管線模型（精簡）

```
Detect (RegionMonitor)
  → Capture (CaptureStage + 背景執行緒)
    → Process (TranslationPipeline + 有界佇列)
      → Present (主 UI / Overlay / OBS)
```

- **force**（按鈕／熱鍵）：可清掉等待中的 auto Process 工作；不因「文字未變」跳過。
- **auto**（監控）：可跳過未變畫面；Process 忙碌時進有界佇列。
- **兩層緩衝**：CaptureStage 延遲再擷取 ≠ Process 佇列深度；「還有多少 OCR/翻譯在等」以 Process `queue_depth` 為準。

細節與狀態轉換見 `docs/state-machine.md`。

## 程式風格

- **Ruff 是格式與 lint 的權威**；不要為了個人風格對抗 formatter。
- 設定在 `pyproject.toml` 的 `[tool.ruff]` / `[tool.ruff.lint]` / `[tool.ruff.format]`。
- 慣例：`from __future__ import annotations`、型別註解、模組內聚。
- 使用者可見字串走 `app/i18n`（`zh-Hant.json` / `en.json`），新增 UI 文案請補雙語 key。
- 刻意的 soft-fail（`try` / `except Exception: pass` 在 UI／Win32 邊界）已列入 Ruff ignore（如 `SIM105`）；不要為了「消 warning」改寫錯誤處理語意，除非是真 bug。
- 新增第三方套件須寫進 `pyproject.toml` 並 `uv lock`；Windows 專用擷取／OCR 路徑是設計一部分，未經要求不要「抽象掉 Win32／WGC」。

## 設定與機密

- 執行期設定：`config.json`（專案根，與 `start.bat` 同層）— **已 gitignore**。
- 環境變數範本：`.env.example` → 本機 `.env`（勿提交 key）。
- 勿提交：`config.json`、`.env`、API key、`models/`、`tools/oneocr/`、`debug_ocr/`、擷取截圖。

## 測試

- 位置：`tests/`；`[tool.pytest.ini_options]` 設 `pythonpath = ["."]`、`testpaths = ["tests"]`。
- 優先為純邏輯寫單測（config、queue、cache、DPI、hotkey parse 等）。
- 依賴本機 OCR DLL／重型引擎的測試可 skip；不要為了綠燈在 CI 沒有的環境硬依賴 GPU／Paddle。
- 變更行為後跑：`uv run pytest -q`。

## 變更準則

1. **最小 diff**：只改任務所需；不做順便大重構。
2. 不動產品行為時，優先只動格式／文件／設定。
3. 邏輯變更：測過再收工；文件契約變了就更新 `README` 或 `docs/`。
4. 不要提交產生物與大型二進位（模型、OneOCR DLL、debug 圖）。

## 文件地圖

| 文件 | 用途 |
|------|------|
| `README.md` | 使用者安裝、功能、API／OCR 說明 |
| `docs/architecture.md` | 模組圖、管線、緩衝語意 |
| `docs/state-machine.md` | Capture／事件狀態 |
| `AGENTS.md`（本檔） | Agent／開發工作流與約束 |
