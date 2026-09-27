from fastapi.testclient import TestClient

from relay.main import DESIGN_DIR, DESIGN_FILES, DESIGN_FONTS, app

client = TestClient(app)


def test_every_design_file_is_served_with_its_type():
    for name, media in DESIGN_FILES.items():
        r = client.get(f"/design/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith(media), name


def test_tokens_load_their_fonts_from_the_fonts_route():
    css = client.get("/design/tokens.css").text
    for name in DESIGN_FONTS:
        assert f'url("fonts/{name}")' in css
        r = client.get(f"/design/fonts/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"] == "font/woff2"
        assert r.content[:4] == b"wOF2"


def test_unknown_design_files_are_refused():
    for path in ("/design/shield.svg", "/design/../README.md", "/design/fonts/OFL.txt", "/design/fonts/x.woff2"):
        assert client.get(path).status_code == 404, path


def test_the_font_licence_ships_with_the_fonts():
    assert (DESIGN_DIR / "fonts" / "OFL.txt").is_file()
