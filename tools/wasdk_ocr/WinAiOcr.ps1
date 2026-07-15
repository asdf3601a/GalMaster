# Windows AI TextRecognizer host - same stack as Win11 Snipping Tool Text actions.
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File WinAiOcr.ps1 -ImagePath <png>
# Exit codes: 0 ok, 10 bootstrap, 12 type missing, 14 imagebuffer, 15 recognize, 20 access denied
param(
    [Parameter(Mandatory = $true)][string]$ImagePath
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bootDll = Join-Path $scriptDir "Microsoft.WindowsAppRuntime.Bootstrap.dll"
if (-not (Test-Path $bootDll)) {
    $bootDll = Join-Path (Join-Path $scriptDir "..\wasdk") "Microsoft.WindowsAppRuntime.Bootstrap.dll"
}
if (-not (Test-Path $bootDll)) {
    [Console]::Error.WriteLine("Missing Microsoft.WindowsAppRuntime.Bootstrap.dll")
    exit 11
}

$bootEsc = $bootDll.Replace('\', '\\')
Add-Type @"
using System.Runtime.InteropServices;
public static class WasdkBootstrap {
  [DllImport("$bootEsc", CharSet = CharSet.Unicode, ExactSpelling = true)]
  public static extern int MddBootstrapInitialize(uint majorMinorVersion, string versionTag, ulong minVersion);
  [DllImport("$bootEsc", ExactSpelling = true)]
  public static extern void MddBootstrapShutdown();
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool SetDllDirectory(string lpPathName);
}
"@

[WasdkBootstrap]::SetDllDirectory((Split-Path -Parent $bootDll)) | Out-Null

$versions = @(0x00010008, 0x00010007, 0x00020002, 0x00010006)
$hr = -1
foreach ($v in $versions) {
    try { $hr = [WasdkBootstrap]::MddBootstrapInitialize([uint32]$v, $null, [uint64]0) } catch { $hr = -1 }
    if ($hr -ge 0) { break }
}
if ($hr -lt 0) {
    [Console]::Error.WriteLine(("MddBootstrapInitialize failed 0x{0:X8}" -f [uint32]$hr))
    exit 10
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime

    $asTaskGeneric = (
        [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.IsGenericMethod -and
            $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        }
    )[0]
    if (-not $asTaskGeneric) { throw "AsTask not found" }

    function Await-Op($winRtTask, [Type]$resultType) {
        $asTask = $asTaskGeneric.MakeGenericMethod($resultType)
        $netTask = $asTask.Invoke($null, @($winRtTask))
        $netTask.GetAwaiter().GetResult()
    }

    $null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.DataWriter, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]

    $full = [System.IO.Path]::GetFullPath($ImagePath)
    $bytes = [System.IO.File]::ReadAllBytes($full)

    $mem = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $writer = New-Object Windows.Storage.Streams.DataWriter($mem)
    $writer.WriteBytes($bytes)
    Await-Op ($writer.StoreAsync()) ([UInt32]) | Out-Null
    Await-Op ($writer.FlushAsync()) ([Boolean]) | Out-Null
    $writer.DetachStream() | Out-Null
    $mem.Seek(0)

    $decoder = Await-Op ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($mem)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Op ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

    $trType = [Type]::GetType(
        "Microsoft.Windows.AI.Imaging.TextRecognizer, Microsoft.Windows.AI.Imaging, ContentType=WindowsRuntime",
        $false)
    if (-not $trType) {
        [Console]::Error.WriteLine("TextRecognizer type not found (Windows AI Imaging not available).")
        exit 12
    }

    try {
        $ensure = $trType.GetMethod("EnsureReadyAsync")
        if ($ensure) {
            $ensureOp = $ensure.Invoke($null, $null)
            $ret = $ensure.ReturnType
            if ($ret.IsGenericType) {
                Await-Op $ensureOp ($ret.GetGenericArguments()[0]) | Out-Null
            }
        }
    } catch {
        # continue
    }

    $create = $null
    foreach ($m in $trType.GetMethods()) {
        if ($m.Name -eq "CreateAsync" -and $m.GetParameters().Count -eq 0) { $create = $m; break }
    }
    if (-not $create) {
        [Console]::Error.WriteLine("TextRecognizer.CreateAsync not found")
        exit 12
    }

    try {
        $createOp = $create.Invoke($null, $null)
        $createRet = $create.ReturnType
        $recogType = if ($createRet.IsGenericType) { $createRet.GetGenericArguments()[0] } else { $trType }
        $recognizer = Await-Op $createOp $recogType
    } catch {
        $msg = "$_"
        $isDenied = $false
        if ($msg.Contains("Denied")) { $isDenied = $true }
        if ($msg.Contains("Unauthorized")) { $isDenied = $true }
        if ($msg.Contains("Access is denied")) { $isDenied = $true }
        if ($msg.Contains("0x80070005")) { $isDenied = $true }
        if ($isDenied) {
            [Console]::Error.WriteLine(
                "Windows AI OCR Access Denied (unpackaged process). " +
                "Snipping Tool is a packaged app; this API may require package identity. " +
                $msg)
            exit 20
        }
        [Console]::Error.WriteLine("CreateAsync failed: $msg")
        exit 16
    }

    $ibType = [Type]::GetType(
        "Microsoft.Graphics.Imaging.ImageBuffer, Microsoft.Graphics.Imaging, ContentType=WindowsRuntime",
        $false)
    if (-not $ibType) {
        [Console]::Error.WriteLine("ImageBuffer type not found")
        exit 13
    }

    $imageBuffer = $null
    foreach ($name in @("CreateForSoftwareBitmap", "CreateBufferAttachedToBitmap")) {
        $m = $ibType.GetMethod($name)
        if (-not $m) { continue }
        try {
            $imageBuffer = $m.Invoke($null, @($bitmap))
            if ($imageBuffer) { break }
        } catch {}
    }
    if (-not $imageBuffer) {
        [Console]::Error.WriteLine("Failed to create ImageBuffer")
        exit 14
    }

    $recognize = $recognizer.GetType().GetMethod("RecognizeTextFromImage")
    if (-not $recognize) {
        [Console]::Error.WriteLine("RecognizeTextFromImage missing")
        exit 15
    }
    $recognized = $recognize.Invoke($recognizer, @($imageBuffer))
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($line in $recognized.Lines) {
        if ($line.Text) { [void]$parts.Add([string]$line.Text) }
    }
    [Console]::Out.Write(($parts -join [Environment]::NewLine))
    exit 0
}
finally {
    try { [WasdkBootstrap]::MddBootstrapShutdown() } catch {}
}
