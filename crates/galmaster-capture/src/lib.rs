//! Cross-platform window / monitor capture with ROI crop.

use anyhow::{anyhow, Context, Result};
use galmaster_core::config::{ScaleFilter, WindowMatchMode};
use galmaster_core::types::{Frame, NormRect, WindowId};
use image::imageops::FilterType;
use image::{DynamicImage, RgbaImage};
use std::path::Path;
use std::time::Instant;
use tracing::debug;
use xcap::{Monitor, Window};

fn to_image_filter(filter: ScaleFilter) -> FilterType {
    match filter {
        ScaleFilter::Nearest => FilterType::Nearest,
        ScaleFilter::Bilinear => FilterType::Triangle,
        ScaleFilter::Bicubic => FilterType::CatmullRom,
        ScaleFilter::Lanczos => FilterType::Lanczos3,
    }
}

#[derive(Debug, Clone)]
pub struct WindowInfo {
    pub id: WindowId,
    pub title: String,
    /// Process executable file name when available (e.g. `vlc.exe`).
    pub exe_name: String,
    pub width: u32,
    pub height: u32,
}

/// How to resolve the capture source.
#[derive(Debug, Clone)]
pub struct CaptureTarget {
    pub pattern: String,
    pub mode: WindowMatchMode,
}

impl CaptureTarget {
    pub fn from_config(pattern: impl Into<String>, mode: WindowMatchMode) -> Self {
        Self {
            pattern: pattern.into(),
            mode,
        }
    }
}

/// List capturable windows (best-effort).
pub fn list_windows() -> Result<Vec<WindowInfo>> {
    let windows = Window::all().context("enumerate windows")?;
    let mut out = Vec::with_capacity(windows.len());
    for w in windows {
        let title = w.title().unwrap_or_default();
        if title.trim().is_empty() {
            continue;
        }
        let exe_name = process_exe_name(&w).unwrap_or_default();
        out.push(WindowInfo {
            id: WindowId(format!("{title}|{exe_name}")),
            title,
            exe_name,
            width: w.width().unwrap_or(0),
            height: w.height().unwrap_or(0),
        });
    }
    out.sort_by_key(|b| std::cmp::Reverse(b.width));
    Ok(out)
}

#[cfg(windows)]
fn process_exe_name(w: &Window) -> Option<String> {
    let pid = w.pid().ok()?;
    process_exe_name_for_pid(pid)
}

/// Quiet process image base name (no xcap app_name → no ERROR spam).
#[cfg(windows)]
fn process_exe_name_for_pid(pid: u32) -> Option<String> {
    use windows::core::PWSTR;
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
        PROCESS_QUERY_LIMITED_INFORMATION,
    };

    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buf = [0u16; 512];
        let mut size = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(
            handle,
            PROCESS_NAME_WIN32,
            PWSTR(buf.as_mut_ptr()),
            &mut size,
        );
        let _ = CloseHandle(handle);
        if ok.is_err() || size == 0 {
            return None;
        }
        let path = String::from_utf16_lossy(&buf[..size as usize]);
        Path::new(&path)
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
    }
}

#[cfg(not(windows))]
fn process_exe_name(w: &Window) -> Option<String> {
    // Linux/macOS: xcap app_name is typically quiet enough.
    w.app_name().ok().filter(|s| !s.trim().is_empty())
}

fn window_matches(w: &Window, target: &CaptureTarget) -> bool {
    let pat = target.pattern.trim();
    if pat.is_empty() {
        return false;
    }
    let pat_l = pat.to_lowercase();
    match target.mode {
        WindowMatchMode::TitleContains => {
            let title = w.title().unwrap_or_default();
            title.to_lowercase().contains(&pat_l)
        }
        WindowMatchMode::TitleExact => {
            let title = w.title().unwrap_or_default();
            title.eq_ignore_ascii_case(pat)
        }
        WindowMatchMode::Executable => {
            let Some(exe) = process_exe_name(w) else {
                return false;
            };
            let exe_l = exe.to_lowercase();
            let stem = Path::new(&exe)
                .file_stem()
                .map(|s| s.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            exe_l == pat_l
                || stem == pat_l
                || exe_l.contains(&pat_l)
                || stem.contains(&pat_l)
        }
    }
}

/// Find first window matching the capture target.
pub fn find_window(target: &CaptureTarget) -> Result<Option<Window>> {
    if target.pattern.trim().is_empty() {
        return Ok(None);
    }
    let windows = Window::all().context("enumerate windows")?;
    for w in windows {
        if window_matches(&w, target) {
            return Ok(Some(w));
        }
    }
    Ok(None)
}

/// Capture primary monitor full frame (no ROI).
pub fn capture_primary_monitor() -> Result<RgbaImage> {
    let monitors = Monitor::all().context("enumerate monitors")?;
    let mon = monitors
        .into_iter()
        .next()
        .ok_or_else(|| anyhow!("no monitor found"))?;
    let img = mon.capture_image().context("capture monitor")?;
    Ok(rgba_from_xcap(img))
}

/// Capture a window by target, or primary monitor if pattern empty / not found.
pub fn capture_target(target: &CaptureTarget) -> Result<(RgbaImage, Option<WindowId>)> {
    if let Some(w) = find_window(target)? {
        let title = w.title().unwrap_or_default();
        let exe = process_exe_name(&w).unwrap_or_default();
        let id = WindowId(format!("{title}|{exe}"));
        let img = w.capture_image().context("capture window")?;
        return Ok((rgba_from_xcap(img), Some(id)));
    }
    if !target.pattern.trim().is_empty() {
        return Err(anyhow!(
            "no window matched {:?} pattern {:?}",
            target.mode,
            target.pattern
        ));
    }
    let img = capture_primary_monitor()?;
    Ok((img, None))
}

/// Capture and crop to normalized ROI → Frame (no rescaling).
pub fn capture_frame(target: &CaptureTarget, roi: NormRect) -> Result<Frame> {
    capture_frame_scaled(target, roi, 1.0, ScaleFilter::Nearest)
}

/// Capture, crop ROI, then optionally rescale for preview / VLM.
///
/// `scale` of `1.0` keeps the native crop size. Values are clamped to `0.1..=4.0`.
pub fn capture_frame_scaled(
    target: &CaptureTarget,
    roi: NormRect,
    scale: f32,
    filter: ScaleFilter,
) -> Result<Frame> {
    let t0 = Instant::now();
    let (full, source_window) = capture_target(target)?;
    let roi = roi.clamp();
    let cropped = crop_norm(&full, roi)?;
    let image = scale_rgba(&cropped, scale, filter);
    debug!(
        w = image.width(),
        h = image.height(),
        scale,
        filter = filter.as_str(),
        ms = t0.elapsed().as_millis() as u64,
        "captured frame"
    );
    Ok(Frame {
        image,
        captured_at: Instant::now(),
        source_window,
        roi,
    })
}

/// Resize an RGBA image by a uniform scale factor.
pub fn scale_rgba(img: &RgbaImage, scale: f32, filter: ScaleFilter) -> RgbaImage {
    let scale = scale.clamp(0.1, 4.0);
    if (scale - 1.0).abs() < 0.001 {
        return img.clone();
    }
    let nw = ((img.width() as f32) * scale).round().max(1.0) as u32;
    let nh = ((img.height() as f32) * scale).round().max(1.0) as u32;
    if nw == img.width() && nh == img.height() {
        return img.clone();
    }
    image::imageops::resize(img, nw, nh, to_image_filter(filter))
}

/// Convenience: title-contains match (legacy helpers / tests).
pub fn capture_frame_title_contains(title: &str, roi: NormRect) -> Result<Frame> {
    capture_frame(
        &CaptureTarget::from_config(title, WindowMatchMode::TitleContains),
        roi,
    )
}

pub fn crop_norm(img: &RgbaImage, roi: NormRect) -> Result<RgbaImage> {
    let (w, h) = img.dimensions();
    if w == 0 || h == 0 {
        return Err(anyhow!("empty image"));
    }
    let pr = roi.to_pixel(w, h);
    let sub = image::imageops::crop_imm(img, pr.x, pr.y, pr.w, pr.h).to_image();
    Ok(sub)
}

fn rgba_from_xcap(img: image::RgbaImage) -> RgbaImage {
    img
}

/// Encode ROI frame as PNG bytes (for vision APIs).
pub fn frame_to_png_bytes(frame: &Frame) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    let dynimg = DynamicImage::ImageRgba8(frame.image.clone());
    dynimg
        .write_to(
            &mut std::io::Cursor::new(&mut buf),
            image::ImageFormat::Png,
        )
        .context("encode png")?;
    Ok(buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crop_bottom_band() {
        let mut img = RgbaImage::new(100, 100);
        for (x, y, p) in img.enumerate_pixels_mut() {
            *p = image::Rgba([x as u8, y as u8, 0, 255]);
        }
        let roi = NormRect {
            x: 0.0,
            y: 0.8,
            w: 1.0,
            h: 0.2,
        };
        let cropped = crop_norm(&img, roi).unwrap();
        assert_eq!(cropped.width(), 100);
        assert_eq!(cropped.height(), 20);
    }

    #[test]
    fn scale_half_nearest() {
        let img = RgbaImage::from_pixel(40, 20, image::Rgba([10, 20, 30, 255]));
        let out = scale_rgba(&img, 0.5, ScaleFilter::Nearest);
        assert_eq!(out.width(), 20);
        assert_eq!(out.height(), 10);
    }
}
