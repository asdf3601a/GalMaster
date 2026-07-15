# Windows AI OCR host (Snipping Tool stack)

Uses **Microsoft.Windows.AI.Imaging.TextRecognizer** via Windows App SDK bootstrap —
the same OCR family as Windows 11 Snipping Tool “Text actions”.

## Files

| File | Role |
|------|------|
| `WinAiOcr.ps1` | Host script |
| `Microsoft.WindowsAppRuntime.Bootstrap.dll` | WASDK bootstrap (from Microsoft.WindowsAppSDK NuGet) |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (text on stdout) |
| 10 | Bootstrap failed |
| 12 | TextRecognizer type missing |
| 20 | **Access Denied** (common for unpackaged processes) |

## Access Denied

Snipping Tool is a **packaged** Store app with package identity.  
Unpackaged Python/exe processes can often resolve the types after bootstrap, but
`TextRecognizer.CreateAsync` may throw `UnauthorizedAccessException`.

GalMaster’s `windows` OCR engine tries AI first, then falls back to classic
`Windows.Media.Ocr` automatically.
