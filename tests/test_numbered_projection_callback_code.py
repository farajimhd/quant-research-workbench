"""Cache identity checks; controlled callbacks grant no native authority."""
import pytest

from src.backend import historical_runtime_versions as versions


@pytest.fixture(autouse=True)
def clear_callback_cache():
    versions._numbered_projection_for_code.cache_clear()
    yield
    versions._numbered_projection_for_code.cache_clear()


def lookup(callback, number=109):
    return versions._loaded_numbered_projection(
        callback, versions.LOADED_BACKEND_FINGERPRINT, number)


def test_unchanged_callback_reuses_its_own_complete_result():
    calls = []
    def certificate(number):
        calls.append(number)
        return 'a' * 64
    assert lookup(certificate) == lookup(certificate) == 'a' * 64
    assert calls == [109]


def test_distinct_number_does_not_inherit_cached_result():
    calls = []
    def certificate(number):
        calls.append(number)
        return str(number)
    assert lookup(certificate, 109) == '109'
    assert lookup(certificate, 110) == '110'
    assert calls == [109, 110]


def test_replacing_code_on_same_function_cannot_reuse_previous_proof():
    def certificate(number):
        return 'a' * 64
    def replacement(number):
        return 'b' * 64
    previous_code = certificate.__code__
    try:
        assert lookup(certificate) == 'a' * 64
        certificate.__code__ = replacement.__code__
        assert lookup(certificate) == 'b' * 64
        assert versions._numbered_projection_for_code.cache_info().misses == 2
    finally:
        certificate.__code__ = previous_code


def test_code_mutation_during_cold_proof_is_rejected():
    def replacement(number):
        assert certificate is not None and replacement is not None
        return 'b' * 64
    def certificate(number):
        certificate.__code__ = replacement.__code__
        return 'a' * 64
    previous_code = certificate.__code__
    try:
        with pytest.raises(RuntimeError, match='changed during proof'):
            lookup(certificate)
    finally:
        certificate.__code__ = previous_code


def test_changed_loaded_fingerprint_rejects_even_a_warm_cache(monkeypatch):
    def certificate(number):
        return 'a' * 64
    previous_fingerprint = versions.LOADED_BACKEND_FINGERPRINT
    assert lookup(certificate) == 'a' * 64
    monkeypatch.setattr(versions, 'LOADED_BACKEND_FINGERPRINT', '0' * 64)
    with pytest.raises(RuntimeError, match='source changed'):
        versions._loaded_numbered_projection(certificate, previous_fingerprint, 109)


def test_callback_exception_is_not_cached():
    calls = []
    def certificate(number):
        calls.append(number)
        raise ValueError('incomplete source proof')
    for _ in range(2):
        with pytest.raises(ValueError, match='incomplete source proof'):
            lookup(certificate)
    assert calls == [109, 109]
