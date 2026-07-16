from app.i18n import set_language, tr


def test_tr_zh_and_en():
    set_language("zh-Hant")
    assert "監控" in tr("btn.start_monitor") or "開始" in tr("btn.start_monitor")
    set_language("en")
    assert "Start" in tr("btn.start_monitor")
    # missing key falls back to key
    assert tr("nonexistent.key.xyz") == "nonexistent.key.xyz"
