import json
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

import app as application
from cover import CoverSettings, CoverStore, MAX_BYTES, date_values, decode_image, render_cover

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def picture(size=(800, 439), color="#448844", fmt="PNG"):
    result = BytesIO()
    Image.new("RGB", size, color).save(result, fmt)
    return result.getvalue()


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = CoverStore(tmp_path, "https://example.org/original.jpg", FONT)
    monkeypatch.setattr(application, "cover_store", store)
    return store


@pytest.fixture
def client(store):
    # Do not start the scheduler in HTTP tests; publication is tested explicitly below.
    return TestClient(application.app)


def send_form(client, endpoint, settings, image=None):
    files = {"image": ("lombardia.png", image, "image/png")} if image is not None else None
    return client.post(endpoint, data={"settings": settings.model_dump_json()}, files=files)


def test_preview_is_side_effect_free_and_preserves_full_image(client, store):
    settings = CoverSettings(source="upload", text_enabled=False, background="#ffffff")
    response = send_form(client, "/cover/preview", settings, picture())
    assert response.status_code == 200
    image = Image.open(BytesIO(response.content))
    assert image.size == (1600, 900)
    assert image.format == "JPEG"
    # Original aspect is slightly wider: white bands, green centre, no crop.
    assert image.getpixel((800, 0))[0] > 240
    assert image.getpixel((800, 450))[1] > image.getpixel((800, 450))[0]
    assert not list(store.directory.iterdir())
    assert response.headers["cache-control"] == "no-store"


def test_save_reload_and_preview_are_identical(client, store):
    settings = CoverSettings(source="upload", title="Meteo {giorno}", subtitle="{regione}",
                             title_x=25, title_y=15, text_color="#ffeedd")
    data = picture()
    before = send_form(client, "/cover/preview", settings, data)
    result = send_form(client, "/cover/settings", settings, data)
    assert result.status_code == 200
    assert result.json()["has_upload"]
    reloaded = CoverStore(store.directory, "https://different.example/default.jpg", FONT)
    assert reloaded.load() == settings
    assert reloaded.has_upload()
    assert client.get("/cover/preview").content == before.content
    assert sorted(p.name for p in store.directory.iterdir()) == ["background.png", "cover.json"]
    # Saving text only retains the already uploaded base image.
    updated = settings.model_copy(update={"title": "Nuovo titolo {data}"})
    assert send_form(client, "/cover/settings", updated).status_code == 200
    assert store.load().title == updated.title


def test_replace_image_invalid_upload_keeps_saved_state(client, store):
    settings = CoverSettings(source="upload", text_enabled=False)
    assert send_form(client, "/cover/settings", settings, picture()).status_code == 200
    previous_settings = (store.directory / "cover.json").read_bytes()
    previous_image = (store.directory / "background.png").read_bytes()
    invalid = send_form(client, "/cover/settings", settings, b"not a picture")
    assert invalid.status_code == 400
    assert "immagine" in invalid.json()["error"]
    assert (store.directory / "cover.json").read_bytes() == previous_settings
    assert (store.directory / "background.png").read_bytes() == previous_image
    assert send_form(client, "/cover/settings", settings, picture(color="#dd3300")).status_code == 200
    assert (store.directory / "background.png").read_bytes() != previous_image


def test_missing_upload_does_not_silently_use_default(client):
    response = send_form(client, "/cover/preview", CoverSettings(source="upload"))
    assert response.status_code == 400
    assert "Carica" in response.json()["error"]


@pytest.mark.parametrize("changes", [
    {"title_x": 101}, {"title_y": -1}, {"title_size": 0}, {"stroke_width": 11},
    {"box_opacity": 101}, {"text_color": "red"}, {"image_url": "file:///etc/passwd"},
    {"title": "{data.__class__}"}, {"subtitle": "{unknown}"}, {"title": "{data:1000}"},
    {"title": "{"}, {"source": "../data"}, {"title_x": float("nan")},
])
def test_validation_rejects_invalid_settings(changes):
    with pytest.raises(ValidationError):
        CoverSettings(**changes)


def test_date_uses_italian_and_updates_every_day():
    day = datetime(2026, 10, 28, 7, tzinfo=ZoneInfo("Europe/Rome"))
    assert date_values(day, "Lombardia") == {
        "data": "Mercoledì 28 ottobre 2026", "giorno": "28/10/2026", "regione": "Lombardia"}
    assert date_values(day.replace(day=29))["data"] == "Giovedì 29 ottobre 2026"


def test_default_url_compatibility_and_network_error(client, store, monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [picture()]
    get = Mock(return_value=response)
    monkeypatch.setattr("cover.requests.get", get)
    assert store.load().image_url == store.default_url
    assert client.get("/cover/preview").status_code == 200
    assert get.call_args.args[0] == store.default_url
    import requests
    get.side_effect = requests.Timeout("network timeout")
    failure = client.get("/cover/preview")
    assert failure.status_code == 400
    assert "scaricare" in failure.json()["error"]


def test_actual_wordpress_job_uses_saved_cover_and_cleans_temp(store, monkeypatch):
    settings = CoverSettings(source="upload", title="Meteo {giorno}", subtitle="Montagne & Paesi")
    store.save(settings, picture())
    monkeypatch.setattr(application, "fetch_meteo_oggi", lambda: [])
    monkeypatch.setattr(application, "WP_USER", "test-user")
    monkeypatch.setattr(application, "WP_APP_PASSWORD", "test-password")
    captured = {}

    def post(url, **kwargs):
        response = Mock(status_code=201)
        if "files" in kwargs:
            name, stream, mime = kwargs["files"]["file"]
            captured["path"] = Path(stream.name)
            captured["bytes"] = stream.read()
            assert mime == "image/jpeg"
            response.json.return_value = {"id": 123}
        else:
            captured["post"] = kwargs["json"]
            response.json.return_value = {"link": "https://example.org/meteo"}
        return response

    monkeypatch.setattr(application.requests, "post", post)
    application.run_job()
    assert application.status.last_error is None
    assert captured["bytes"] == store.render(values=date_values(regione=application.REGIONE))
    assert Image.open(BytesIO(captured["bytes"])).size == (1600, 900)
    assert captured["post"]["featured_media"] == 123
    assert not captured["path"].exists()
    assert not application.status.running
    assert not application.job_lock.locked()


def test_concurrent_job_is_not_published_twice(store, monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(application, "fetch_meteo_oggi", fetch)
    application.job_lock.acquire()
    try:
        application.run_job()
        fetch.assert_not_called()
    finally:
        application.job_lock.release()


def test_transparency_and_cover_fit():
    data = BytesIO()
    Image.new("RGBA", (600, 600), (0, 0, 0, 0)).save(data, "PNG")
    settings = CoverSettings(text_enabled=False, background="#ffffff", fit="cover")
    result = render_cover(decode_image(data.getvalue()), settings, date_values(), FONT)
    assert Image.open(BytesIO(result)).getpixel((800, 450)) == (255, 255, 255)


def test_upload_limit_and_malformed_settings(client):
    response = client.post("/cover/preview", headers={"content-length": str(MAX_BYTES + 2 * 1024 * 1024)})
    assert response.status_code == 400
    response = client.post("/cover/settings", data={"settings": json.dumps({"unexpected": True})})
    assert response.status_code == 400
    assert "unexpected" in response.json()["error"]


def test_panel_renders_saved_controls_and_escapes_text(client, store):
    settings = CoverSettings(source="upload", title="</script><script>alert(1)</script>")
    store.save(settings, picture())
    response = client.get("/")
    assert response.status_code == 200
    assert "SALVA GRAFICA" in response.text
    assert "</script><script>alert(1)</script>" not in response.text
    assert 'id="cover-preview"' in response.text
