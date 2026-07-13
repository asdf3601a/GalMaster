//! Discover system fonts for fallback chains.
//! Supports TTF/OTF and TTC (face index 0) for Windows CJK collections.

use std::path::{Path, PathBuf};

const MAX_FALLBACKS: usize = 8;

/// (egui font key, path, ttc face index)
pub fn discover_system_fallback_fonts() -> Vec<(String, PathBuf, u32)> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();

    for (key, path, index) in known_candidates() {
        if out.len() >= MAX_FALLBACKS {
            break;
        }
        let p = PathBuf::from(path);
        if !p.is_file() || !seen.insert(p.clone()) {
            continue;
        }
        if font_file_usable(&p, index) {
            out.push((key.into(), p, index));
        }
    }

    if out.len() < MAX_FALLBACKS {
        for dir in font_dirs() {
            scan_dir(&dir, &mut out, &mut seen, MAX_FALLBACKS);
            if out.len() >= MAX_FALLBACKS {
                break;
            }
        }
    }

    out
}

fn font_file_usable(path: &Path, index: u32) -> bool {
    let Ok(bytes) = std::fs::read(path) else {
        return false;
    };
    if bytes.len() < 12 {
        return false;
    }
    let sig = &bytes[0..4];
    // sfnt TrueType / OpenType / Apple true / collection ttcf
    let is_sfnt = matches!(
        sig,
        [0x00, 0x01, 0x00, 0x00] | b"true" | b"typ1" | b"OTTO"
    );
    let is_ttc = sig == b"ttcf";
    if !is_sfnt && !is_ttc {
        return false;
    }
    if is_ttc && bytes.len() >= 12 {
        // ttcf header: tag(4) + version(4) + numFonts(4)
        let num = u32::from_be_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]);
        if num == 0 || index >= num {
            return false;
        }
    }
    true
}

fn font_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    #[cfg(windows)]
    {
        if let Ok(windir) = std::env::var("WINDIR") {
            dirs.push(PathBuf::from(windir).join("Fonts"));
        } else {
            dirs.push(PathBuf::from(r"C:\Windows\Fonts"));
        }
    }
    #[cfg(target_os = "macos")]
    {
        dirs.push(PathBuf::from("/System/Library/Fonts/Supplemental"));
        dirs.push(PathBuf::from("/Library/Fonts"));
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        dirs.push(PathBuf::from("/usr/share/fonts"));
        dirs.push(PathBuf::from("/usr/local/share/fonts"));
        if let Ok(home) = std::env::var("HOME") {
            dirs.push(PathBuf::from(home).join(".local/share/fonts"));
        }
    }
    dirs
}

/// Prefer known UI/CJK fonts. TTC uses face index 0 (Regular).
fn known_candidates() -> Vec<(&'static str, &'static str, u32)> {
    #[cfg(windows)]
    {
        vec![
            // Single-font TTF first
            ("sys_segoe", r"C:\Windows\Fonts\segoeui.ttf", 0),
            ("sys_segoeb", r"C:\Windows\Fonts\segoeuib.ttf", 0),
            ("sys_arial", r"C:\Windows\Fonts\arial.ttf", 0),
            ("sys_arialuni", r"C:\Windows\Fonts\ARIALUNI.TTF", 0),
            ("sys_tahoma", r"C:\Windows\Fonts\tahoma.ttf", 0),
            ("sys_malgun", r"C:\Windows\Fonts\malgun.ttf", 0),
            // CJK collections (index 0)
            ("sys_yahei", r"C:\Windows\Fonts\msyh.ttc", 0),
            ("sys_yahei_light", r"C:\Windows\Fonts\msyhl.ttc", 0),
            ("sys_jhenghei", r"C:\Windows\Fonts\msjh.ttc", 0),
            ("sys_meiryo", r"C:\Windows\Fonts\meiryo.ttc", 0),
            ("sys_yugoth", r"C:\Windows\Fonts\YuGothM.ttc", 0),
            ("sys_msgothic", r"C:\Windows\Fonts\msgothic.ttc", 0),
            ("sys_msmincho", r"C:\Windows\Fonts\msmincho.ttc", 0),
        ]
    }
    #[cfg(target_os = "macos")]
    {
        vec![
            (
                "sys_arialuni",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                0,
            ),
            ("sys_pingfang", "/System/Library/Fonts/PingFang.ttc", 0),
        ]
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        vec![
            (
                "sys_noto_sans",
                "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                0,
            ),
            (
                "sys_noto_sc",
                "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
                0,
            ),
            (
                "sys_noto_tc",
                "/usr/share/fonts/opentype/noto/NotoSansTC-Regular.otf",
                0,
            ),
            (
                "sys_dejavu",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                0,
            ),
            (
                "sys_noto_cjk",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                0,
            ),
        ]
    }
}

fn scan_dir(
    dir: &Path,
    out: &mut Vec<(String, PathBuf, u32)>,
    seen: &mut std::collections::HashSet<PathBuf>,
    budget: usize,
) {
    if out.len() >= budget || !dir.is_dir() {
        return;
    }
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for ent in rd.flatten() {
        if out.len() >= budget {
            break;
        }
        let path = ent.path();
        if path.is_dir() {
            continue;
        }
        let ext = path
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !matches!(ext.as_str(), "ttf" | "otf" | "ttc") {
            continue;
        }
        if !seen.insert(path.clone()) {
            continue;
        }
        let fname = path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        let interesting = fname.contains("noto")
            || fname.contains("cjk")
            || fname.contains("arial")
            || fname.contains("segoe")
            || fname.contains("msyh")
            || fname.contains("msjh")
            || fname.contains("malgun")
            || fname.contains("dejavu");
        if interesting && font_file_usable(&path, 0) {
            out.push((format!("sys_scan_{}", out.len()), path, 0));
        }
    }
}

pub fn css_font_stack(user_family: &str) -> String {
    let user = user_family.trim();
    if user.is_empty() || user.eq_ignore_ascii_case("system") {
        "\"Segoe UI\", \"Microsoft YaHei UI\", \"Microsoft YaHei\", \"Microsoft JhengHei\", \"Noto Sans CJK TC\", sans-serif".into()
    } else {
        format!(
            "\"{user}\", \"Segoe UI\", \"Microsoft YaHei\", \"Microsoft JhengHei\", sans-serif"
        )
    }
}
