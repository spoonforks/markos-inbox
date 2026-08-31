from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_removed_feature_has_no_routes_imports_labels_or_assets() -> None:
    forbidden = "thought" + "box"
    island = "thought" + "_island"
    old_route = "/api/" + "thoughts/"
    candidates = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".venv" not in path.parts
        and path.suffix.lower() in {".py", ".js", ".html", ".css", ".md", ".yaml", ".yml"}
        and path != Path(__file__)
    ]
    for path in candidates:
        content = path.read_text(encoding="utf-8", errors="ignore").lower()
        assert forbidden not in content, path
        assert island not in content, path
        assert old_route not in content, path
    assert not any(island in path.name.lower() for path in ROOT.rglob("*"))


def test_pwa_never_caches_api_or_places_token_in_a_url() -> None:
    script = (ROOT / "app/static/js/capture.js").read_text(encoding="utf-8")
    worker = (ROOT / "app/static/js/capture-sw.js").read_text(encoding="utf-8")
    assert "Authorization" in script
    assert "localStorage" in script
    assert "?token=" not in script
    assert 'url.pathname.startsWith("/api/")' in worker
