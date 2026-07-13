use crate::system_fonts::{css_font_stack, discover_system_fallback_fonts};
use eframe::egui;
use galmaster_core::style::{Align, ShadowStyle, SubtitleStyle};
use std::path::PathBuf;
use std::sync::OnceLock;

pub fn style_editor_ui(ui: &mut egui::Ui, style: &mut SubtitleStyle) -> bool {
    let mut changed = false;

    ui.heading("Subtitle style");

    ui.label(egui::RichText::new("Font").strong());
    ui.horizontal(|ui| {
        ui.label("Family name");
        changed |= ui.text_edit_singleline(&mut style.font_family).changed();
    });
    ui.horizontal(|ui| {
        if ui
            .button("System default")
            .on_hover_text("Clear custom file; use OS fonts as fallback")
            .clicked()
        {
            style.font_path = None;
            style.font_family = "System".into();
            changed = true;
        }
        let using_sys = style.uses_system_font_fallback();
        ui.label(if using_sys {
            egui::RichText::new("→ system fallback chain").weak()
        } else {
            egui::RichText::new("→ custom file + system fallback").weak()
        });
    });

    ui.horizontal(|ui| {
        ui.label("Custom font file");
        let mut path = style
            .font_path
            .as_ref()
            .map(|p| p.display().to_string())
            .unwrap_or_default();
        if ui.text_edit_singleline(&mut path).changed() {
            style.font_path = if path.trim().is_empty() {
                None
            } else {
                Some(PathBuf::from(path.trim()))
            };
            changed = true;
        }
        if ui.small_button("Clear").clicked() {
            style.font_path = None;
            changed = true;
        }
    });

    ui.horizontal(|ui| {
        ui.label("Size (px)");
        changed |= ui
            .add(egui::Slider::new(&mut style.font_size_px, 12.0..=120.0))
            .changed();
    });

    let mut orig_size = style.original_font_size_px.unwrap_or(22.0);
    ui.horizontal(|ui| {
        ui.label("Original size");
        if ui
            .add(egui::Slider::new(&mut orig_size, 10.0..=80.0))
            .changed()
        {
            style.original_font_size_px = Some(orig_size);
            changed = true;
        }
    });

    ui.separator();
    ui.label(egui::RichText::new("Colors & background").strong());
    ui.horizontal(|ui| {
        ui.label("Text");
        changed |= color_edit(ui, &mut style.color);
        ui.label("Background RGB");
        // Edit RGB only, keep current alpha via opacity slider
        let (r, g, b, a) = style.background_rgba();
        let mut rgb = [r, g, b];
        if ui.color_edit_button_rgb(&mut rgb).changed() {
            style.background = SubtitleStyle::with_alpha(
                &format!(
                    "#{:02X}{:02X}{:02X}FF",
                    (rgb[0] * 255.0) as u8,
                    (rgb[1] * 255.0) as u8,
                    (rgb[2] * 255.0) as u8
                ),
                a,
            );
            changed = true;
        }
    });

    let mut bg_opacity = style.background_opacity();
    ui.horizontal(|ui| {
        ui.label("Background opacity");
        if ui
            .add(egui::Slider::new(&mut bg_opacity, 0.0..=1.0).text(""))
            .changed()
        {
            style.set_background_opacity(bg_opacity);
            changed = true;
        }
        ui.label(format!("{:.0}%", bg_opacity * 100.0));
    });
    ui.horizontal(|ui| {
        if ui.button("Transparent (0%)").clicked() {
            style.set_background_opacity(0.0);
            changed = true;
        }
        if ui.button("Soft (60%)").clicked() {
            style.set_background_opacity(0.6);
            changed = true;
        }
        if ui.button("Solid (100%)").clicked() {
            style.set_background_opacity(1.0);
            changed = true;
        }
    });
    ui.small(format!("background = {}", style.background));

    ui.horizontal(|ui| {
        ui.label("Outline (0 = off)");
        changed |= ui
            .add(egui::Slider::new(&mut style.outline_px, 0.0..=8.0))
            .on_hover_text("Faux stroke around glyphs. 0 disables. Prefer small values (1–3).")
            .changed();
        if style.outline_px > 0.05 {
            changed |= color_edit(ui, &mut style.outline_color);
        }
    });

    let mut has_shadow = style.shadow.is_some();
    if ui.checkbox(&mut has_shadow, "Shadow").changed() {
        style.shadow = if has_shadow {
            Some(ShadowStyle::default())
        } else {
            None
        };
        changed = true;
    }

    ui.horizontal(|ui| {
        ui.label("Align");
        for (label, a) in [
            ("Left", Align::Left),
            ("Center", Align::Center),
            ("Right", Align::Right),
        ] {
            if ui.selectable_label(style.align == a, label).clicked() {
                style.align = a;
                changed = true;
            }
        }
    });

    ui.horizontal(|ui| {
        changed |= ui
            .checkbox(&mut style.show_original, "Show original")
            .changed();
        changed |= ui
            .checkbox(&mut style.show_translated, "Show translated")
            .changed();
    });

    ui.horizontal(|ui| {
        ui.label("Line height");
        changed |= ui
            .add(egui::Slider::new(&mut style.line_height, 0.8..=2.0))
            .changed();
    });

    ui.separator();
    ui.label("Preview");
    draw_subtitle_preview(ui, style, Some("Original sample 原文"), "譯文預覽 Sample");

    changed
}

fn color_edit(ui: &mut egui::Ui, hex: &mut String) -> bool {
    let (r, g, b, a) = SubtitleStyle::parse_color(hex);
    let mut rgba = [r, g, b, a];
    let response = ui.color_edit_button_rgba_unmultiplied(&mut rgba);
    if response.changed() {
        *hex = format!(
            "#{:02X}{:02X}{:02X}{:02X}",
            (rgba[0] * 255.0) as u8,
            (rgba[1] * 255.0) as u8,
            (rgba[2] * 255.0) as u8,
            (rgba[3] * 255.0) as u8
        );
        true
    } else {
        false
    }
}

/// Draw subtitle with optional outline.
///
/// Outline must re-layout text in the outline color: Galley meshes bake glyph
/// colors, so reusing the fill-color galley at offsets only creates white
/// ghosts (looks like broken rendering) instead of a stroke.
pub fn draw_subtitle_preview(
    ui: &mut egui::Ui,
    style: &SubtitleStyle,
    original: Option<&str>,
    translated: &str,
) {
    let (cr, cg, cb, ca) = style.color_rgba();
    let (br, bg, bb, ba) = style.background_rgba();
    let (or, og, ob, oa) = style.outline_rgba();

    let text_color = egui::Color32::from_rgba_unmultiplied(
        (cr * 255.0) as u8,
        (cg * 255.0) as u8,
        (cb * 255.0) as u8,
        (ca * 255.0) as u8,
    );
    let bg_color = egui::Color32::from_rgba_unmultiplied(
        (br * 255.0) as u8,
        (bg * 255.0) as u8,
        (bb * 255.0) as u8,
        (ba * 255.0) as u8,
    );
    let outline_color = egui::Color32::from_rgba_unmultiplied(
        (or * 255.0) as u8,
        (og * 255.0) as u8,
        (ob * 255.0) as u8,
        (oa * 255.0) as u8,
    );

    let align = match style.align {
        Align::Left => egui::Align::LEFT,
        Align::Center => egui::Align::Center,
        Align::Right => egui::Align::RIGHT,
    };

    let mut lines: Vec<(String, f32)> = Vec::new();
    if style.show_original {
        if let Some(o) = original {
            if !o.is_empty() {
                lines.push((o.to_string(), style.original_font_size_px.unwrap_or(22.0)));
            }
        }
    }
    if style.show_translated && !translated.is_empty() {
        lines.push((translated.to_string(), style.font_size_px));
    }
    if lines.is_empty() {
        return;
    }

    // Cap outline so a huge slider value cannot swamp the glyph.
    let outline_w = style
        .outline_px
        .clamp(0.0, (style.font_size_px * 0.25).max(0.0));

    let mut max_w = 0.0f32;
    let mut total_h = 16.0f32 + outline_w * 2.0;
    // (fill_galley, outline_galley optional)
    let mut prepared: Vec<(std::sync::Arc<egui::Galley>, Option<std::sync::Arc<egui::Galley>>)> =
        Vec::new();

    for (text, size) in &lines {
        let font = egui::FontId::proportional(*size);
        let fill = ui.fonts(|f| f.layout_no_wrap(text.clone(), font.clone(), text_color));
        max_w = max_w.max(fill.size().x);
        total_h += fill.size().y * style.line_height.max(1.0);

        let outline_galley = if outline_w > 0.05 {
            Some(ui.fonts(|f| f.layout_no_wrap(text.clone(), font, outline_color)))
        } else {
            None
        };
        prepared.push((fill, outline_galley));
    }
    total_h += 8.0;
    max_w += 24.0 + outline_w * 2.0;

    let (rect, _resp) = ui.allocate_exact_size(egui::vec2(max_w, total_h), egui::Sense::hover());
    let painter = ui.painter();

    if ba > 0.001 {
        painter.rect_filled(rect, 8.0, bg_color);
    }

    // Evenly spaced ring samples for a smoother stroke than 8-neighborhood.
    let outline_offsets = outline_offsets(outline_w);

    let mut y = rect.min.y + 8.0 + outline_w;
    for (fill_galley, outline_galley) in prepared {
        let gh = fill_galley.size().y;
        let x = match align {
            egui::Align::LEFT => rect.min.x + 12.0 + outline_w,
            egui::Align::Center => rect.center().x - fill_galley.size().x * 0.5,
            _ => rect.max.x - 12.0 - outline_w - fill_galley.size().x,
        };
        let pos = egui::pos2(x, y);

        if let Some(og) = outline_galley {
            for (dx, dy) in &outline_offsets {
                painter.galley(pos + egui::vec2(*dx, *dy), og.clone(), outline_color);
            }
        }
        // Fill on top (galley already baked with text_color).
        painter.galley(pos, fill_galley, text_color);
        y += gh * style.line_height.max(1.0);
    }
}

/// Unit-circle offsets for faux text stroke. Empty when width ≈ 0.
fn outline_offsets(width: f32) -> Vec<(f32, f32)> {
    if width <= 0.05 {
        return Vec::new();
    }
    // More samples for thicker outlines; keep cost bounded.
    let n = if width < 1.5 {
        8
    } else if width < 3.5 {
        12
    } else {
        16
    };
    (0..n)
        .map(|i| {
            let a = std::f32::consts::TAU * (i as f32) / (n as f32);
            (a.cos() * width, a.sin() * width)
        })
        .collect()
}

pub fn apply_custom_font(ctx: &egui::Context, style: &SubtitleStyle) {
    apply_fonts(ctx, style);
}

fn try_insert_font(
    fonts: &mut egui::FontDefinitions,
    key: &str,
    bytes: Vec<u8>,
    index: u32,
    chain: &mut Vec<String>,
) -> bool {
    if bytes.len() < 8 {
        return false;
    }
    let sig = &bytes[0..4];
    let ok_sig = matches!(
        sig,
        [0x00, 0x01, 0x00, 0x00] | b"true" | b"typ1" | b"OTTO" | b"ttcf"
    );
    if !ok_sig {
        tracing::debug!(key, "skip font (unsupported signature)");
        return false;
    }
    let mut data = egui::FontData::from_owned(bytes);
    data.index = index; // face index inside .ttc collections (0 = first face)
    fonts
        .font_data
        .insert(key.to_owned(), std::sync::Arc::new(data));
    chain.push(key.to_owned());
    true
}

pub fn apply_fonts(ctx: &egui::Context, style: &SubtitleStyle) {
    // Start from egui defaults so Latin always works even if system CJK load fails.
    let mut fonts = egui::FontDefinitions::default();
    let mut primary_chain: Vec<String> = Vec::new();

    // 1) Optional user file (TTC index 0)
    if let Some(path) = style.font_path.as_ref() {
        if !path.as_os_str().is_empty() {
            match std::fs::read(path) {
                Ok(bytes) => {
                    if !try_insert_font(
                        &mut fonts,
                        "galmaster_custom",
                        bytes,
                        0,
                        &mut primary_chain,
                    ) {
                        tracing::warn!(path = %path.display(), "custom font not usable by egui");
                    }
                }
                Err(e) => {
                    tracing::warn!(path = %path.display(), error = %e, "custom font unreadable");
                }
            }
        }
    }

    // 2) System fallbacks (TTF/OTF + TTC index 0 for YaHei etc.)
    static SYS: OnceLock<Vec<(String, PathBuf, u32)>> = OnceLock::new();
    let sys = SYS.get_or_init(discover_system_fallback_fonts);
    for (key, path, index) in sys.iter() {
        if primary_chain.len() >= 10 {
            break;
        }
        match std::fs::read(path) {
            Ok(bytes) => {
                let _ = try_insert_font(&mut fonts, key, bytes, *index, &mut primary_chain);
            }
            Err(_) => continue,
        }
    }

    // Prepend successful fonts; keep egui built-ins as final fallback.
    if let Some(prop) = fonts.families.get_mut(&egui::FontFamily::Proportional) {
        for name in primary_chain.iter().rev() {
            prop.retain(|n| n != name);
            prop.insert(0, name.clone());
        }
    }
    if let Some(mono) = fonts.families.get_mut(&egui::FontFamily::Monospace) {
        for name in &primary_chain {
            if !mono.contains(name) {
                mono.push(name.clone());
            }
        }
    }

    ctx.set_fonts(fonts);
    tracing::info!(
        loaded = primary_chain.len(),
        fonts = ?primary_chain,
        "font fallback chain ready"
    );
    let _ = css_font_stack(&style.font_family);
}
