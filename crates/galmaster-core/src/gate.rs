use crate::types::Frame;
use image::RgbaImage;
use strsim::normalized_levenshtein;

/// Skip model calls when ROI pixels barely change.
#[derive(Debug, Clone)]
pub struct FrameGate {
    pub pixel_diff_threshold: f32,
    last_hash: Option<u64>,
    last_sample: Option<Vec<u8>>,
    sample_stride: u32,
}

impl Default for FrameGate {
    fn default() -> Self {
        Self {
            pixel_diff_threshold: 0.01,
            last_hash: None,
            last_sample: None,
            sample_stride: 8,
        }
    }
}

impl FrameGate {
    pub fn new(pixel_diff_threshold: f32) -> Self {
        Self {
            pixel_diff_threshold,
            ..Default::default()
        }
    }

    /// Returns true if the frame is novel enough to process.
    pub fn should_process(&mut self, frame: &Frame) -> bool {
        let sample = downsample_luma(&frame.image, self.sample_stride);
        let hash = fnv1a64(&sample);

        if let (Some(prev_hash), Some(prev_sample)) = (self.last_hash, self.last_sample.as_ref()) {
            if prev_hash == hash {
                return false;
            }
            let diff = mean_abs_diff(prev_sample, &sample);
            if diff < self.pixel_diff_threshold {
                return false;
            }
        }

        self.last_hash = Some(hash);
        self.last_sample = Some(sample);
        true
    }

    pub fn reset(&mut self) {
        self.last_hash = None;
        self.last_sample = None;
    }
}

/// Stabilize extracted text: require consensus frames + novelty vs last accepted.
#[derive(Debug, Clone)]
pub struct TextGate {
    pub similarity_skip: f32,
    pub stable_frames: u32,
    pending: Option<String>,
    pending_count: u32,
    last_accepted: Option<String>,
}

impl Default for TextGate {
    fn default() -> Self {
        Self {
            similarity_skip: 0.92,
            stable_frames: 2,
            pending: None,
            pending_count: 0,
            last_accepted: None,
        }
    }
}

impl TextGate {
    pub fn new(similarity_skip: f32, stable_frames: u32) -> Self {
        Self {
            similarity_skip,
            stable_frames: stable_frames.max(1),
            ..Default::default()
        }
    }

    /// Feed a candidate string. Returns Some(stable_text) when ready to translate.
    pub fn push(&mut self, text: &str) -> Option<String> {
        let text = normalize_text(text);
        if text.is_empty() {
            self.pending = None;
            self.pending_count = 0;
            return None;
        }

        if let Some(last) = &self.last_accepted {
            if similarity(last, &text) >= self.similarity_skip as f64 {
                // Same as last accepted — ignore.
                self.pending = Some(text);
                self.pending_count = 0;
                return None;
            }
        }

        match &self.pending {
            Some(p) if similarity(p, &text) >= self.similarity_skip as f64 => {
                self.pending_count += 1;
                // Refresh pending to latest OCR noise-smoothed form
                self.pending = Some(text.clone());
                if self.pending_count >= self.stable_frames {
                    self.last_accepted = Some(text.clone());
                    self.pending_count = 0;
                    return Some(text);
                }
            }
            _ => {
                self.pending = Some(text);
                self.pending_count = 1;
                if self.stable_frames <= 1 {
                    let t = self.pending.clone().unwrap();
                    self.last_accepted = Some(t.clone());
                    return Some(t);
                }
            }
        }
        None
    }

    pub fn reset(&mut self) {
        self.pending = None;
        self.pending_count = 0;
        self.last_accepted = None;
    }

    pub fn last_accepted(&self) -> Option<&str> {
        self.last_accepted.as_deref()
    }
}

/// Novelty gate for e2e vision results (original + translated).
#[derive(Debug, Clone)]
pub struct ResultGate {
    pub similarity_skip: f32,
    last_key: Option<String>,
}

impl Default for ResultGate {
    fn default() -> Self {
        Self {
            similarity_skip: 0.92,
            last_key: None,
        }
    }
}

impl ResultGate {
    pub fn new(similarity_skip: f32) -> Self {
        Self {
            similarity_skip,
            last_key: None,
        }
    }

    /// Returns true if the result is novel.
    pub fn accept(&mut self, original: Option<&str>, translated: &str) -> bool {
        let key = format!(
            "{}||{}",
            original.unwrap_or("").trim(),
            translated.trim()
        );
        if let Some(last) = &self.last_key {
            if similarity(last, &key) >= self.similarity_skip as f64 {
                return false;
            }
        }
        self.last_key = Some(key);
        true
    }

    pub fn reset(&mut self) {
        self.last_key = None;
    }
}

fn normalize_text(s: &str) -> String {
    s.lines()
        .map(|l| l.trim())
        .filter(|l| !l.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn similarity(a: &str, b: &str) -> f64 {
    if a == b {
        return 1.0;
    }
    normalized_levenshtein(a, b)
}

fn downsample_luma(img: &RgbaImage, stride: u32) -> Vec<u8> {
    let stride = stride.max(1);
    let (w, h) = img.dimensions();
    let mut out = Vec::with_capacity(((w / stride + 1) * (h / stride + 1)) as usize);
    let mut y = 0;
    while y < h {
        let mut x = 0;
        while x < w {
            let p = img.get_pixel(x, y).0;
            // Rec. 601 luma
            let luma = (0.299 * p[0] as f32 + 0.587 * p[1] as f32 + 0.114 * p[2] as f32) as u8;
            out.push(luma);
            x += stride;
        }
        y += stride;
    }
    out
}

fn mean_abs_diff(a: &[u8], b: &[u8]) -> f32 {
    let n = a.len().min(b.len());
    if n == 0 {
        return 1.0;
    }
    let mut sum = 0u64;
    for i in 0..n {
        sum += (a[i] as i16 - b[i] as i16).unsigned_abs() as u64;
    }
    // also penalize length mismatch lightly
    let len_pen = (a.len() as i64 - b.len() as i64).unsigned_abs() as f32 * 255.0;
    ((sum as f32 + len_pen) / (n as f32 * 255.0)).min(1.0)
}

fn fnv1a64(data: &[u8]) -> u64 {
    const FNV_OFFSET: u64 = 0xcbf29ce484222325;
    const FNV_PRIME: u64 = 0x100000001b3;
    let mut hash = FNV_OFFSET;
    for b in data {
        hash ^= *b as u64;
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{Rgba, RgbaImage};

    fn solid_frame(color: [u8; 4]) -> Frame {
        let mut img = RgbaImage::new(64, 32);
        for p in img.pixels_mut() {
            *p = Rgba(color);
        }
        Frame {
            image: img,
            captured_at: std::time::Instant::now(),
            source_window: None,
            roi: Default::default(),
        }
    }

    #[test]
    fn frame_gate_skips_identical() {
        let mut gate = FrameGate::new(0.01);
        let f1 = solid_frame([10, 20, 30, 255]);
        let f2 = solid_frame([10, 20, 30, 255]);
        assert!(gate.should_process(&f1));
        assert!(!gate.should_process(&f2));
    }

    #[test]
    fn frame_gate_accepts_change() {
        let mut gate = FrameGate::new(0.01);
        let f1 = solid_frame([0, 0, 0, 255]);
        let f2 = solid_frame([255, 255, 255, 255]);
        assert!(gate.should_process(&f1));
        assert!(gate.should_process(&f2));
    }

    #[test]
    fn text_gate_requires_stable_frames() {
        let mut gate = TextGate::new(0.92, 2);
        assert!(gate.push("hello").is_none());
        assert_eq!(gate.push("hello").as_deref(), Some("hello"));
        // Same as accepted → skip
        assert!(gate.push("hello").is_none());
        // Novel text needs 2 frames
        assert!(gate.push("world").is_none());
        assert_eq!(gate.push("world").as_deref(), Some("world"));
    }

    #[test]
    fn text_gate_merges_ocr_noise() {
        let mut gate = TextGate::new(0.85, 2);
        assert!(gate.push("こんにちは").is_none());
        // High similarity should count toward consensus
        assert_eq!(gate.push("こんにちは").as_deref(), Some("こんにちは"));
    }

    #[test]
    fn result_gate_dedup() {
        let mut gate = ResultGate::new(0.92);
        assert!(gate.accept(Some("hi"), "你好"));
        assert!(!gate.accept(Some("hi"), "你好"));
        assert!(gate.accept(Some("bye"), "再見"));
    }
}
