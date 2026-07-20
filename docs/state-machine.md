# State machines

## Application pipeline (logical)

```
         region_changed / hotkey / translate button
                         │
         ┌───────────────▼────────────────┐
         │  Idle                          │
         │    │ request capture           │
         │    ▼                           │
         │  Capturing  (optional cloak)   │
         │    │ image ready / error       │
         │    ▼                           │
         │  Process (Running + wait queue)│
         │    │ finished                  │
         │    ▼                           │
         │  Present → back to Idle*       │
         └────────────────────────────────┘
```

\* Present is synchronous on the UI thread; the app may still be Process-busy if more jobs are queued.

## CaptureStage (`app/session/capture_stage.py`)

### Phases

| Phase | Meaning |
|-------|---------|
| `Idle` | No grab in flight |
| `Capturing` | Grab scheduled or running (including brief Overlay cloak delay) |

### Fields

| Field | Meaning |
|-------|---------|
| `pending_force` | Force flag for the **current** grab |
| `deferred_auto` | Number of auto recaptures to run after current grab (capped) |
| `pending_force_recapture` | After current grab, start a force grab next |
| `overlay_was_visible` / cloak flags | Overlay restore after capture |

### Events

| Event | From | Action |
|-------|------|--------|
| `request(force, buffer_cap)` while Idle | Idle → Capturing | Start grab; if force, caller also clears Process auto queue |
| `request(force)` while Capturing | stay Capturing | force → set recapture; auto → `deferred_auto++` if under cap |
| `finish()` | Capturing → Idle | Clear capturing; caller enqueues Process then `pump()` |
| `pump()` while Idle | Idle → Capturing if work pending | Prefer force recapture over deferred auto |

### Sequence (happy path)

1. User/monitor → `translate_now(force=…)`
2. `CaptureStage.request` accepts or defers
3. Controller cloaks Overlay if needed → timer → background `capture_from_config`
4. `_on_capture_finished` → `stage.finish()` → `pipeline.request(cfg, img, force=…)`
5. `stage.pump()` may start another capture

## RegionMonitor (Detect)

Implicit states inside the monitor loop:

| State | Trigger | Next |
|-------|---------|------|
| Baseline | First frame | Idle sampling |
| Idle sampling | `diff < threshold` | stay / progress status |
| Change seen | `diff ≥ threshold` | If stable_ms=0 → fire (cooldown permitting); else WaitingStable |
| WaitingStable | Quiet long enough | Fire if cooldown ok |
| Cooldown | Fired recently | Status only until cooldown ends |

Signals: `region_changed`, `status`, `error`.

## TranslationPipeline (Process)

| State | Meaning |
|-------|---------|
| Idle (`busy=False`) | No job running; queue empty |
| Running (`busy=True`) | Worker executing one job |
| Running + queue | Additional `PipelineJob`s waiting (depth ≤ buffer) |

`force` job enqueue drops waiting non-force jobs. See `pipeline_queue.enqueue_job`.

Worker outcomes (`PipelineResult`): success, `skipped` (unchanged/blank/empty OCR), soft `error` (e.g. LLM), hard error without source.

## Present rules (summary)

| Result | UI |
|--------|-----|
| `skipped` | Status only; keep last overlay/result |
| Hard error, no source | Status only; keep last content |
| Soft LLM error + source | Show source + error text |
| `ocr_only` | Show OCR; no translation |
| Success | Update main, overlay, OBS |

## Event sources → entry points

| Source | Entry |
|--------|--------|
| Hotkey / tray / “立即翻譯” | `translate_now(force=True)` |
| `RegionMonitor.region_changed` | `translate_now(force=False)` |
| Capture thread done | `_on_capture_finished` (drops auto if monitor off) |
| Pipeline worker done | `on_pipeline_finished` → `_present` |
| Stop monitor | `monitor.stop` + `pipeline.cancel()` (clear queue + abort in-flight) |
| Apply / Save / Cancel | settings runtime sync (monitor, hotkey, OBS) |
