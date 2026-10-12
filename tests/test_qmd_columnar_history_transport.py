import asyncio
from dataclasses import replace
from threading import Event

import polars as pl
from polars.testing import assert_frame_equal
import pytest

from src.backend import qmd_columnar_history_transport as t
from src.market_engine import columnar_history_product as c
from test_columnar_history_publication import Store, publish, consume, prepared_source
from pipelines.market_sip.events.columnar_history_producer import wire_records


def response_for(certificate, first, stop, field_ids=None):
    prepared = certificate.prepared
    result = prepared.clocks.slice(first, stop - first).to_dict(as_series=False)
    for name in ("source_known", "observed", "extremes_valid"):
        result[name] = [bool(v) for v in result[name]]
    result.update(contract=t.CONTRACT, coverage=wire_records(c.COVERAGE, [dict(certificate.header)])[0],
        first_ordinal=first, stop_ordinal=stop, observed_ordinal=list(range(first, stop)), fields=[])
    for field in prepared.fields:
        if field_ids is not None and field.field_id not in field_ids:
            continue
        runs = prepared.values.filter((pl.col("field_id") == field.field_id)
            & (pl.col("first_ordinal") < stop) & (pl.col("stop_ordinal") > first)).sort("first_ordinal")
        result["fields"].append(dict(field_id=field.field_id, field_name=field.name, unit=field.unit,
            first_ordinal=runs["first_ordinal"].to_list(), stop_ordinal=runs["stop_ordinal"].to_list(),
            value_bits=runs["value_bits"].to_list(), valid=runs["valid"].cast(pl.Boolean).to_list()))
    return result


def test_selected_fields_keep_full_product_seal_and_exact_float32_values(certificate):
    pin = certificate.pin()
    field = pin.fields[0]
    names = (field.name,)
    request = t.window_request(pin, first_ordinal=0, stop_ordinal=100,
        as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), field_names=names)
    assert request["field_ids"] == [field.field_id]
    assert request["expected_content_hash"] == pin.header["content_hash"]
    response = response_for(certificate, 0, 100, request["field_ids"])
    decoded = t.decode_window(pin, response, first_ordinal=0, stop_ordinal=100,
                              evaluation_block="origin", field_names=names)
    full = t.decode_window(pin, response_for(certificate, 0, 100), first_ordinal=0,
                           stop_ordinal=100, evaluation_block="origin")
    assert decoded[field.name].equals(full[field.name])
    assert decoded[field.name + "__valid"].equals(full[field.name + "__valid"])
    assert all(other.name not in decoded.columns for other in pin.fields[1:])
    with pytest.raises(ValueError, match="field population"):
        t.decode_window(pin, response_for(certificate, 0, 100), first_ordinal=0,
                        stop_ordinal=100, evaluation_block="origin", field_names=names)
    with pytest.raises(ValueError, match="outside its certified"):
        t.selected_history_fields(pin, ("missing",))


@pytest.fixture
def certificate(prepared_source):
    store = Store()
    publish(store, prepared_source)
    return consume(store, prepared_source)


def test_qmd_column_arrays_reconstruct_the_exact_cold_product_without_feature_calculation(certificate):
    calls = []
    def post(path, request, timeout):
        calls.append((path, request, timeout))
        return response_for(certificate, request["first_ordinal"], request["stop_ordinal"])
    pin = certificate.pin()
    frame = t.read_history_window(pin, first_ordinal=50, stop_ordinal=150, evaluation_block="explicit-block",
        as_of=c.completed_clock(pin.scope, pin.scope.first_bucket + 149), history_post=post)
    expected = certificate.window(first_ordinal=50, stop_ordinal=150, evaluation_block="explicit-block")
    assert_frame_equal(frame, expected, check_exact=True)
    assert calls[0][0] == t.PATH
    assert calls[0][1]["expected_content_hash"] == pin.header["content_hash"]
    assert calls[0][1]["field_ids"] == [f.field_id for f in pin.fields]
    assert calls[0][1]["scope"] == {**pin.scope.prefix(), "session_date": pin.scope.session_date.isoformat()}
    assert calls[0][2] == 45.


@pytest.mark.parametrize("corruption", ["future_clock", "mask", "coverage", "catalog", "interval", "infinite_bits", "length", "extra"])
def test_transport_rejects_causal_or_typed_contract_drift(certificate, corruption):
    response = response_for(certificate, 0, 200)
    if corruption == "future_clock":
        response["bucket_index"][-1] += 1
    elif corruption == "mask":
        response["source_known"][0] = 1
    elif corruption == "coverage":
        response["coverage"]["producer_code_hash"] = "a" * 64
    elif corruption == "catalog":
        response["fields"][0]["unit"] = "price"
    elif corruption == "interval":
        response["fields"][0]["first_ordinal"][0] = 1
    elif corruption == "infinite_bits":
        response["fields"][0]["value_bits"][0] = 0x7f800000
        response["fields"][0]["valid"][0] = True
    elif corruption == "length":
        response["rolling_volume"].pop()
    else:
        response["unapproved"] = []
    with pytest.raises(ValueError):
        t.decode_window(certificate.pin(), response, first_ordinal=0, stop_ordinal=200, evaluation_block="block")


def test_future_or_invalid_requests_fail_before_network_io(certificate):
    pin = certificate.pin()
    calls = []
    def post(*a, **k):
        calls.append(a)
        raise AssertionError("network must not run")
    with pytest.raises(ValueError, match="as-of"):
        t.read_history_window(pin, first_ordinal=0, stop_ordinal=200, evaluation_block="block",
            as_of=c.completed_clock(pin.scope, pin.scope.first_bucket + 198), history_post=post)
    with pytest.raises(ValueError, match="evaluation-block"):
        t.read_history_window(pin, first_ordinal=0, stop_ordinal=200, evaluation_block="",
            as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), history_post=post)
    assert not calls


def test_cold_pin_does_not_allow_a_substituted_catalog(certificate):
    pin = certificate.pin()
    fields = tuple(sorted((c.BY_NAME["history.attention_mean.57"], pin.fields[1]), key=lambda f: f.field_id))
    with pytest.raises(ValueError, match="cold coverage"):
        replace(pin, fields=fields)


def test_async_http_cancel_drains_the_owned_transport(certificate):
    entered, release = Event(), Event()
    def post(path, request, timeout):
        entered.set()
        release.wait(5)
        return response_for(certificate, 0, 200)
    async def scenario():
        pin = certificate.pin()
        task = asyncio.create_task(t.read_history_window_async(pin, first_ordinal=0, stop_ordinal=200,
            evaluation_block="block", as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), history_post=post))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_running_service_requires_the_approved_source_and_new_input_contract():
    health = dict(running=True, status="ready", service="qmd_history_gateway", host_role="historical",
        columnar_history_contract=t.CONTRACT, columnar_history_cold_certification_required=True,
        source_fingerprint="a" * 64)
    def get(path, timeout):
        assert path == "/health" and timeout == 15.
        return health
    assert t.verify_history_service(expected_source_fingerprint="a" * 64, history_get=get) == "a" * 64
    for key, value in (("source_fingerprint", "b" * 64), ("columnar_history_contract", "old-contract"),
                       ("columnar_history_cold_certification_required", False), ("status", "failed")):
        original = health[key]
        health[key] = value
        with pytest.raises(ValueError, match="running source"):
            t.verify_history_service(expected_source_fingerprint="a" * 64, history_get=get)
        health[key] = original
