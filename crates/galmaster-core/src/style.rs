use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum Align {
    Left,
    #[default]
    Center,
    Right,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShadowStyle {
    pub offset_x: f32,
    pub offset_y: f32,
    pub blur: f32,
    /// #AARRGGBB or #RRGGBB or #RRGGBBAA
    pub color: String,
}

impl Default for ShadowStyle {
    fn default() -> Self {
        Self {
            offset_x: 2.0,
            offset_y: 2.0,
            blur: 4.0,
            color: "#000000AA".into(),
        }
    }
}

/// Shared subtitle presentation style for Overlay + OBS.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubtitleStyle {
    pub font_family: String,
    /// Custom font file; empty/None uses built-in or system fallback.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub font_path: Option<PathBuf>,
    pub font_size_px: f32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub original_font_size_px: Option<f32>,
    /// #RRGGBBAA
    pub color: String,
    pub background: String,
    pub outline_px: f32,
    pub outline_color: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub shadow: Option<ShadowStyle>,
    #[serde(default)]
    pub align: Align,
    pub line_height: f32,
    pub show_original: bool,
    pub show_translated: bool,
}

impl Default for SubtitleStyle {
    fn default() -> Self {
        Self {
            // "System" → OS font fallback chain in UI/OBS (YaHei / Segoe / Noto …)
            font_family: "System".into(),
            font_path: None,
            font_size_px: 42.0,
            original_font_size_px: Some(22.0),
            color: "#FFFFFFFF".into(),
            background: "#00000099".into(),
            // 0 = off. Fake stroke re-layouts glyphs; keep off by default so
            // chroma / plain text stays clean unless the user enables it.
            outline_px: 0.0,
            outline_color: "#000000FF".into(),
            shadow: None,
            align: Align::Center,
            line_height: 1.25,
            show_original: false,
            show_translated: true,
        }
    }
}

impl SubtitleStyle {
    /// Parse #RGB / #RGBA / #RRGGBB / #RRGGBBAA into (r,g,b,a) in 0..1.
    pub fn parse_color(hex: &str) -> (f32, f32, f32, f32) {
        let s = hex.trim().trim_start_matches('#');
        let parse = |i: usize| {
            u8::from_str_radix(&s[i..i + 2], 16).unwrap_or(255) as f32 / 255.0
        };
        match s.len() {
            3 => {
                let r = u8::from_str_radix(&s[0..1].repeat(2), 16).unwrap_or(255) as f32 / 255.0;
                let g = u8::from_str_radix(&s[1..2].repeat(2), 16).unwrap_or(255) as f32 / 255.0;
                let b = u8::from_str_radix(&s[2..3].repeat(2), 16).unwrap_or(255) as f32 / 255.0;
                (r, g, b, 1.0)
            }
            4 => {
                let r = u8::from_str_radix(&s[0..1].repeat(2), 16).unwrap_or(255) as f32 / 255.0;
                let g = u8::from_str_radix(&s[1..2].repeat(2), 16).unwrap_or(255) as f32 / 255.0;
                let b = u8::from_str_radix(&s[2..3].repeat(2), 16).unwrap_or(255) as f32 / 255.0;
                let a = u8::from_str_radix(&s[3..4].repeat(2), 16).unwrap_or(255) as f32 / 255.0;
                (r, g, b, a)
            }
            6 => (parse(0), parse(2), parse(4), 1.0),
            8 => (parse(0), parse(2), parse(4), parse(6)),
            _ => (1.0, 1.0, 1.0, 1.0),
        }
    }

    pub fn color_rgba(&self) -> (f32, f32, f32, f32) {
        Self::parse_color(&self.color)
    }

    pub fn background_rgba(&self) -> (f32, f32, f32, f32) {
        Self::parse_color(&self.background)
    }

    pub fn outline_rgba(&self) -> (f32, f32, f32, f32) {
        Self::parse_color(&self.outline_color)
    }

    /// Background opacity 0.0..=1.0 derived from `background` alpha.
    pub fn background_opacity(&self) -> f32 {
        self.background_rgba().3
    }

    /// Set only the alpha of a `#RRGGBBAA` (or shorter) color string.
    pub fn with_alpha(hex: &str, alpha: f32) -> String {
        let (r, g, b, _) = Self::parse_color(hex);
        let a = alpha.clamp(0.0, 1.0);
        format!(
            "#{:02X}{:02X}{:02X}{:02X}",
            (r * 255.0).round() as u8,
            (g * 255.0).round() as u8,
            (b * 255.0).round() as u8,
            (a * 255.0).round() as u8
        )
    }

    pub fn set_background_opacity(&mut self, opacity: f32) {
        self.background = Self::with_alpha(&self.background, opacity);
    }

    /// True when no custom font file is configured (use system fallback chain).
    pub fn uses_system_font_fallback(&self) -> bool {
        self.font_path
            .as_ref()
            .map(|p| p.as_os_str().is_empty())
            .unwrap_or(true)
    }
}

#[cfg(test)]
mod alpha_tests {
    use super::*;

    #[test]
    fn with_alpha_preserves_rgb() {
        let s = SubtitleStyle::with_alpha("#112233FF", 0.5);
        assert_eq!(&s[0..7], "#112233");
        let (_, _, _, a) = SubtitleStyle::parse_color(&s);
        assert!((a - 0.5).abs() < 0.02);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_rrggbbaa() {
        let (r, g, b, a) = SubtitleStyle::parse_color("#FF000080");
        assert!((r - 1.0).abs() < 0.01);
        assert!(g < 0.01);
        assert!(b < 0.01);
        assert!((a - 0.5).abs() < 0.02);
    }
}
