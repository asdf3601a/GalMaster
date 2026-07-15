# Probe classic WinRT OCR + Windows AI TextRecognizer availability
$ErrorActionPreference = "Stop"

Write-Host "=== Classic Windows.Media.Ocr ==="
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($eng) {
    Write-Host ("classic lang: " + $eng.RecognizerLanguage.LanguageTag)
} else {
    Write-Host "classic eng: null"
}

Write-Host "=== Look for Microsoft.Windows.AI.Imaging.winmd ==="
$candidates = @(
    "C:\Program Files\WindowsApps\Microsoft.WindowsAppRuntime.2_2.2.0.0_x64__8wekyb3d8bbwe\Microsoft.Windows.AI.Imaging.winmd",
    "C:\Program Files\WindowsApps\Microsoft.WindowsAppRuntime.1.8_8000.921.1539.0_x64__8wekyb3d8bbwe\Microsoft.Windows.AI.Imaging.winmd",
    "C:\Windows\SystemApps\Microsoft.WindowsAppRuntime.vNext.CBS_8wekyb3d8bbwe\Microsoft.Windows.AI.Imaging.winmd"
)
foreach ($c in $candidates) {
    Write-Host ("exists " + (Test-Path $c) + " " + $c)
}

# Try loading via Reflection on DLL
Write-Host "=== Try LoadFrom Imaging.dll ==="
$dll = "C:\Program Files\WindowsApps\Microsoft.WindowsAppRuntime.2_2.2.0.0_x64__8wekyb3d8bbwe\Microsoft.Windows.AI.Imaging.dll"
if (Test-Path $dll) {
    try {
        $asm = [System.Reflection.Assembly]::LoadFrom($dll)
        Write-Host ("loaded asm: " + $asm.FullName)
        $types = $asm.GetExportedTypes() | Select-Object -First 30 FullName
        $types | ForEach-Object { Write-Host ("  type " + $_) }
    } catch {
        Write-Host ("LoadFrom failed: " + $_.Exception.Message)
    }
}

Write-Host "=== dotnet --list-sdks ==="
try { & dotnet --list-sdks } catch { Write-Host "no dotnet" }
