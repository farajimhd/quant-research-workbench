from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from src.backend.app import real_live_trading_logo
from src.backend.real_live_market_data.startup import cached_logo_asset_exists, logo_asset_url


def test_logo_asset_response_is_immutable_and_cacheable(tmp_path: Path, monkeypatch) -> None:
    asset = tmp_path / "massive" / "icon" / "aapl-contenthash.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"png")
    monkeypatch.setattr(
        "src.backend.app.market_gateway_config",
        lambda: SimpleNamespace(logo_artifact_root=str(tmp_path)),
    )

    response = real_live_trading_logo("massive/icon/aapl-contenthash.png")

    assert Path(response.path) == asset
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_missing_logo_asset_is_negative_cached_briefly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.backend.app.market_gateway_config",
        lambda: SimpleNamespace(logo_artifact_root=str(tmp_path)),
    )

    try:
        real_live_trading_logo("massive/icon/missing-contenthash.png")
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.headers == {"Cache-Control": "public, max-age=60"}
    else:
        raise AssertionError("Missing logo should return HTTP 404")


def test_logo_url_is_only_published_for_an_existing_asset(tmp_path: Path) -> None:
    cached_logo_asset_exists.cache_clear()
    asset = tmp_path / "massive" / "icon" / "aapl-contenthash.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"png")

    assert logo_asset_url(
        "massive/icon/aapl-contenthash.png",
        artifact_root=tmp_path,
    ) == "/api/real-live-trading/logo?path=massive%2Ficon%2Faapl-contenthash.png"
    assert logo_asset_url("massive/icon/missing.png", artifact_root=tmp_path) == ""
    assert logo_asset_url("../outside.png", artifact_root=tmp_path) == ""
