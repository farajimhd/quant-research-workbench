from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import math

import pytest

from src.trading_runtime import arte_oms_observation_snapshot as module


def snapshot(fingerprint):
    return module.project_shared_oms_observations(run_id="shared-run",
        session_date=date(2026, 8, 18), checkpoint_sequence=42, boundary_ms=30000,
        groups={"group": SimpleNamespace(broker_order_ids=("broker",),
            broker_order_state_fingerprints={"broker": fingerprint})})


@pytest.mark.parametrize("kind", ["canonical", "live_adapter"])
def test_exact_native_fingerprint_survives_shared_codec(kind):
    numbers = (math.nextafter(0.0, 1.0), math.nextafter(5.0, 6.0),
               math.nextafter(0.001, 1.0), math.nextafter(10.0, 11.0), 0.0)
    fingerprint = (("working", "Submitted", *numbers, "", "", "")
        if kind == "canonical" else ("Submitted", *numbers, ""))
    rows = snapshot(fingerprint)
    assert rows == snapshot(fingerprint)
    codec = module.verified_oms_observation_codec_rows(rows)
    child = codec.observations[0]
    restored = tuple(float(child[name]) for name in module._NUMBERS)
    assert tuple(value.hex() for value in restored) == tuple(value.hex() for value in numbers)
    assert child["fingerprint_kind"] == kind
    assert all("strategy_one" not in table.name for table in module.TABLES)
    assert all(kind == "UInt64" for name, kind in module.OBSERVATION.columns
               if name.endswith("_bits"))
    with pytest.raises(TypeError):
        rows.root["boundary_ms"] = 40000
    with pytest.raises(TypeError):
        rows.observations[0]["warning"] = "changed"


def test_missing_or_mutated_observations_are_rejected():
    rows = snapshot(("working", "Submitted", 0.0, 5.0, 0.0, 10.0, 0.0, "", "", ""))
    with pytest.raises(ValueError, match="seal"):
        module.verify_shared_oms_observations(replace(rows, observations=()))
    with pytest.raises(ValueError, match="seal"):
        module.verify_shared_oms_observations(replace(rows,
            observations=({**rows.observations[0], "warning": "changed"},)))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, True])
def test_invalid_native_observations_fail_closed(value):
    with pytest.raises(ValueError):
        snapshot(("working", "Submitted", 0.0, 5.0, value, 10.0, 0.0, "", "", ""))


def test_empty_observed_set_has_complete_sealed_root():
    rows = module.project_shared_oms_observations(run_id="shared-run",
        session_date=date(2026, 8, 18), checkpoint_sequence=42, boundary_ms=30000,
        groups={})
    assert module.verify_shared_oms_observations(rows).root["observation_count"] == 0
    assert module.verified_oms_observation_codec_rows(rows).observations == ()


def test_zero_uses_existing_canonical_price_domain():
    rows = snapshot(("working", "Submitted", -0.0, 5.0, 0.0, 10.0, 0.0, "", "", ""))
    assert rows.observations[0]["filled_quantity_bits"] == 0


@pytest.mark.parametrize("bits", [True, -1, 2 ** 64, 0x7ff0000000000000,
                                   0x8000000000000000])
def test_resealed_invalid_numeric_bits_are_not_trusted(bits):
    rows = snapshot(("working", "Submitted", 0.0, 5.0, 0.0, 10.0, 0.0, "", "", ""))
    fields = {key: value for key, value in rows.observations[0].items()
              if key != "content_hash"}
    fields["limit_price_bits"] = bits
    children = (module.codec._sealed(fields),)
    malformed = module.SharedOmsObservationSnapshot(module._root(rows.root, children), children)
    with pytest.raises(ValueError):
        module.verify_shared_oms_observations(malformed)
