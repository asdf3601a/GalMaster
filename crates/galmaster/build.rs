// Embed Windows VERSIONINFO + application manifest so the binary looks like
// a normal desktop app (helps reduce "unknown unsigned MinGW EXE" heuristics).

fn main() {
    #[cfg(target_os = "windows")]
    {
        // When cross-compiling, cfg(target_os) is the *host* in build.rs unless
        // we key off the cargo TARGET env. Always set resources when TARGET is Windows.
    }

    let target = std::env::var("TARGET").unwrap_or_default();
    if !target.contains("windows") {
        return;
    }

    let mut res = winresource::WindowsResource::new();
    res.set("ProductName", "GalMaster");
    res.set("FileDescription", "GalMaster — real-time subtitle capture and translation");
    res.set("CompanyName", "GalMaster");
    res.set("LegalCopyright", "Copyright (c) GalMaster contributors");
    res.set("OriginalFilename", "galmaster.exe");
    res.set("InternalName", "galmaster");
    res.set("ProductVersion", env!("CARGO_PKG_VERSION"));
    res.set("FileVersion", env!("CARGO_PKG_VERSION"));

    // Prefer asInvoker — never request admin elevation (admin EXEs get more scrutiny).
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("windows/app.manifest");
    if manifest.exists() {
        res.set_manifest_file(manifest.to_str().unwrap());
    }

    if let Err(e) = res.compile() {
        // Don't hard-fail Linux hosts without windres when building gnu without tools;
        // still print so CI can notice.
        println!("cargo:warning=winresource compile failed: {e}");
    }
}
