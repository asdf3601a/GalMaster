"""Probe Windows AI TextRecognizer via Dynamic Dependencies + WinRT activation."""

from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
import uuid
from ctypes import (
    HRESULT,
    POINTER,
    Structure,
    byref,
    c_int32,
    c_uint32,
    c_uint64,
    c_void_p,
    c_wchar_p,
)
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

kernelbase = ctypes.WinDLL("KernelBase")
combase = ctypes.WinDLL("combase")


class PACKAGE_VERSION(Structure):
    _fields_ = [("Version", c_uint64)]


class GUID(Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, s: str) -> "GUID":
        u = uuid.UUID(s)
        g = cls()
        g.Data1 = u.time_low
        g.Data2 = u.time_mid
        g.Data3 = u.time_hi_version
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g


# Architectures
PDPA_NONE = 0
PDPA_NEUTRAL = 0x1
PDPA_X86 = 0x2
PDPA_X64 = 0x4
PDPA_ARM = 0x8
PDPA_ARM64 = 0x10

# Lifetime
PDL_PROCESS = 1

# Create options
CPDO_NONE = 0
CPDO_DO_NOT_VERIFY = 0x1  # CreatePackageDependencyOptions_DoNotVerifyDependencyResolution?

# Add options
APDO_NONE = 0
APDO_PREPEND_IF_RANK_COLLISION = 0x1


def _get_package_family_names() -> list[str]:
    cmd = (
        "Get-AppxPackage Microsoft.WindowsAppRuntime* | "
        "Select-Object -ExpandProperty PackageFamilyName"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        check=False,
    )
    names = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    # Prefer newer major versions first
    names = sorted(set(names), reverse=True)
    return names


def add_wasdk_dependency() -> tuple[c_void_p, str]:
    TryCreate = kernelbase.TryCreatePackageDependency
    TryCreate.argtypes = [
        c_void_p,
        c_wchar_p,
        PACKAGE_VERSION,
        c_int32,
        c_int32,
        c_wchar_p,
        c_int32,
        POINTER(c_wchar_p),
    ]
    TryCreate.restype = HRESULT

    AddDep = kernelbase.AddPackageDependency
    AddDep.argtypes = [
        c_wchar_p,
        c_int32,
        c_int32,
        POINTER(c_void_p),
        POINTER(c_wchar_p),
    ]
    AddDep.restype = HRESULT

    errors: list[str] = []
    for pfn in _get_package_family_names():
        dep_id = c_wchar_p()
        ver = PACKAGE_VERSION(0)
        arch = PDPA_NEUTRAL | PDPA_X64 | PDPA_X86 | PDPA_ARM64
        hr = TryCreate(
            None,
            pfn,
            ver,
            arch,
            PDL_PROCESS,
            None,
            CPDO_NONE,
            byref(dep_id),
        )
        print(f"TryCreate {pfn} hr={hr & 0xFFFFFFFF:08X} id={dep_id.value}")
        if hr < 0 or not dep_id.value:
            errors.append(f"{pfn}: TryCreate 0x{hr & 0xFFFFFFFF:08X}")
            continue
        ctx = c_void_p()
        full = c_wchar_p()
        hr2 = AddDep(dep_id.value, 0, APDO_NONE, byref(ctx), byref(full))
        print(f"  AddDep hr={hr2 & 0xFFFFFFFF:08X} full={full.value}")
        if hr2 >= 0:
            return ctx, full.value or pfn
        errors.append(f"{pfn}: AddDep 0x{hr2 & 0xFFFFFFFF:08X}")
    raise RuntimeError("AddPackageDependency failed: " + " | ".join(errors))


def ro_init() -> None:
    RoInitialize = combase.RoInitialize
    RoInitialize.argtypes = [c_int32]
    RoInitialize.restype = HRESULT
    hr = RoInitialize(1)  # RO_INIT_MULTITHREADED
    # RPC_E_CHANGED_MODE / S_FALSE ok
    print(f"RoInitialize hr={hr & 0xFFFFFFFF:08X}")


def try_activation_factory(class_name: str) -> c_void_p | None:
    WindowsCreateString = combase.WindowsCreateString
    WindowsCreateString.argtypes = [c_wchar_p, c_uint32, POINTER(c_void_p)]
    WindowsCreateString.restype = HRESULT
    RoGetActivationFactory = combase.RoGetActivationFactory
    RoGetActivationFactory.argtypes = [c_void_p, POINTER(GUID), POINTER(c_void_p)]
    RoGetActivationFactory.restype = HRESULT

    hstr = c_void_p()
    hr = WindowsCreateString(class_name, len(class_name), byref(hstr))
    if hr < 0:
        print(f"WindowsCreateString failed {hr & 0xFFFFFFFF:08X}")
        return None
    # IActivationFactory
    iid = GUID.from_str("00000035-0000-0000-C000-000000000046")
    fac = c_void_p()
    try:
        hr = RoGetActivationFactory(hstr, byref(iid), byref(fac))
    except OSError as exc:
        print(f"RoGetActivationFactory({class_name}) OSError: {exc}")
        return None
    print(f"RoGetActivationFactory({class_name}) hr={hr & 0xFFFFFFFF:08X} fac={fac}")
    if hr < 0 or not fac:
        return None
    return fac


def main() -> int:
    print("PFNs:", _get_package_family_names())
    ro_init()
    print("Before dep, activation:")
    try_activation_factory("Microsoft.Windows.AI.Imaging.TextRecognizer")
    try_activation_factory("Windows.Media.Ocr.OcrEngine")

    ctx, full = add_wasdk_dependency()
    print("Added dependency", full, "ctx", ctx)

    print("After dep, activation:")
    fac = try_activation_factory("Microsoft.Windows.AI.Imaging.TextRecognizer")
    try_activation_factory("Microsoft.Graphics.Imaging.ImageBuffer")
    try_activation_factory("Windows.Media.Ocr.OcrEngine")

    if not fac:
        print("FAILED to get TextRecognizer factory")
        return 1

    print("SUCCESS: TextRecognizer activation factory acquired")
    # Further COM calls need proper vtable binding; try winrt after dependency
    try:
        # dynamic import may still fail without projections
        import importlib

        for mod in [
            "winrt.microsoft.windows.ai.imaging",
            "winrt.windows.media.ocr",
        ]:
            try:
                m = importlib.import_module(mod)
                print("imported", mod, m)
            except Exception as e:
                print("no module", mod, e)
    except Exception as e:
        print("import probe", e)

    # Try using WindowsRuntime System from winrt after package dep
    # Fallback path: call via subprocess C# later
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
