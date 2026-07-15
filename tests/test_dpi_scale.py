from app.capture.dpi import ScaleInfo, physical_size_to_qt, qt_rect_to_physical


def test_identity_scale():
    s = ScaleInfo()
    assert s.is_identity
    assert qt_rect_to_physical(10, 20, 100, 50, s) == (10, 20, 100, 50)


def test_150_percent_scale():
    # Qt 2560x1440 vs mss 3840x2160
    s = ScaleInfo(scale_x=1.5, scale_y=1.5, qt_left=0, qt_top=0, mss_left=0, mss_top=0)
    assert not s.is_identity
    px, py, pw, ph = qt_rect_to_physical(100, 200, 400, 80, s)
    assert (px, py, pw, ph) == (150, 300, 600, 120)
    assert physical_size_to_qt(600, 120, s) == (400, 80)


def test_offset_virtual_desktop():
    s = ScaleInfo(
        scale_x=1.5,
        scale_y=1.5,
        qt_left=-100,
        qt_top=0,
        mss_left=-150,
        mss_top=0,
    )
    px, py, pw, ph = qt_rect_to_physical(0, 10, 50, 20, s)
    # (0 - (-100)) * 1.5 + (-150) = 150 - 150 = 0
    assert px == 0
    assert py == 15
    assert pw == 75
    assert ph == 30
