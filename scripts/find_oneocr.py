from pathlib import Path
import subprocess

r = subprocess.run(
    [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-AppxPackage *ScreenSketch* | Select-Object -ExpandProperty InstallLocation",
    ],
    capture_output=True,
    text=True,
)
print("stdout:", repr(r.stdout))
print("stderr:", repr(r.stderr))
for loc in (r.stdout or "").splitlines():
    loc = loc.strip()
    if not loc:
        continue
    p = Path(loc)
    print("loc exists", p.exists(), p)
    if not p.exists():
        continue
    for h in p.rglob("*"):
        n = h.name.lower()
        if "oneocr" in n or n == "onnxruntime.dll":
            print(f"  {h.relative_to(p)}  {h.stat().st_size}")

# fallback glob
wa = Path(r"C:\Program Files\WindowsApps")
try:
    for d in wa.glob("Microsoft.ScreenSketch*"):
        print("pkg", d)
        for h in d.rglob("*"):
            n = h.name.lower()
            if "oneocr" in n or n == "onnxruntime.dll":
                print(f"  {h}  {h.stat().st_size}")
except Exception as e:
    print("WindowsApps scan", e)
