from app.hotkeys.global_hotkey import MOD_CONTROL, MOD_SHIFT, parse_hotkey


def test_parse_ctrl_shift_t():
    spec = parse_hotkey("Ctrl+Shift+T")
    assert spec.vk == ord("T")
    assert spec.modifiers & MOD_CONTROL
    assert spec.modifiers & MOD_SHIFT


def test_parse_f9():
    spec = parse_hotkey("F9")
    assert spec.vk == 0x78
