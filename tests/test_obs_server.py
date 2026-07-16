import json
import time

from app.obs.server import ObsSubtitleServer


def test_obs_server_start_publish_stop():
    srv = ObsSubtitleServer()
    # Use a high ephemeral-ish port to reduce collision
    port = 18765
    srv.configure(
        show_source=True,
        show_translation=True,
        translation_font_size=24,
        source_font_size=18,
        source_color="#aabbcc",
        bg_alpha=100,
    )
    srv.start(port)
    assert srv.running, srv.last_error
    assert srv.url() == f"http://127.0.0.1:{port}/"
    srv.publish(source="src", translation="tr", status="ok")
    # Brief wait so server accepts
    time.sleep(0.1)
    from urllib.request import urlopen

    with urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    assert data["source"] == "src"
    assert data["translation"] == "tr"
    assert "style" in data
    assert data["style"]["show_source"] is True
    assert data["style"]["translation_font_size"] == 24
    assert data["style"]["source_color"] == "#aabbcc"
    assert data["style"]["bg_alpha"] == 100
    with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
        html = resp.read().decode("utf-8")
    assert "GalMaster OBS" in html
    assert "applyStyle" in html
    srv.stop()
    assert not srv.running


def test_obs_configure_forces_one_visible():
    srv = ObsSubtitleServer()
    srv.configure(show_source=False, show_translation=False)
    st = srv.style_dict()
    assert st["show_translation"] is True
