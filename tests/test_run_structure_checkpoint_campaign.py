from pathlib import Path

from scripts.run_structure_checkpoint_campaign import binary_candidates, parse_launcher_args


def test_prebuilt_runtime_binary_is_preferred_without_cargo() -> None:
    candidates = binary_candidates(None, {"TRADING_RUNTIME_ROOT": r"E:\TradingRuntime"})

    assert candidates[0] == Path(r"E:\TradingRuntime") / "bin" / candidates[0].name


def test_explicit_binary_precedes_environment_and_runtime_defaults() -> None:
    candidates = binary_candidates(
        r"C:\campaign\explicit.exe",
        {
            "QMD_STRUCTURE_CAMPAIGN_BINARY": r"C:\campaign\configured.exe",
            "TRADING_RUNTIME_ROOT": r"E:\TradingRuntime",
        },
    )

    assert str(candidates[0]).endswith("explicit.exe")
    assert str(candidates[1]).endswith("configured.exe")


def test_launcher_options_are_not_forwarded_to_native_campaign() -> None:
    launcher, campaign = parse_launcher_args(
        ["--no-build", "--start-date", "2026-01-01", "--workers", "16"]
    )

    assert launcher.no_build is True
    assert campaign == ["--start-date", "2026-01-01", "--workers", "16"]
