from concurrent.futures import ThreadPoolExecutor

import pytest

from src.trading_runtime.single_publication_verification import SinglePublicationVerification


def fixture(**overrides):
    state = {'input': b'original', 'result': bytearray(b'verified'), 'calls': 0, 'valid': True}

    def original():
        state['calls'] += 1
        return state['result']

    def authority():
        if not state['valid']:
            raise ValueError('Source authority changed')

    callbacks = dict(original=original, input_image=lambda: state['input'],
                     output_image=bytes, require_authority=authority, max_image_bytes=64)
    callbacks.update(overrides)
    return state, SinglePublicationVerification(**callbacks)


def test_one_original_replay_and_scope_cannot_escape():
    state, memo = fixture()
    with memo.scope():
        for _ in range(4):
            assert memo.verify() is state['result']
    assert state['calls'] == 1
    assert memo._result is None
    with pytest.raises(ValueError, match='inactive'):
        memo.verify()
    with pytest.raises(ValueError, match='cannot be reused'):
        with memo.scope():
            pass


@pytest.mark.parametrize('mutation', ['input', 'output', 'authority'])
def test_mutations_reject_without_second_original(mutation):
    state, memo = fixture()
    with pytest.raises(ValueError):
        with memo.scope():
            memo.verify()
            if mutation == 'input': state['input'] = b'changed'
            elif mutation == 'output': state['result'][0] = 0
            else: state['valid'] = False
            memo.verify()
    assert state['calls'] == 1
    assert not memo._active and memo._result is None


def test_changed_callback_code_rejects():
    state, memo = fixture()
    function = memo._callbacks[0]
    def replacement():
        return state['result']
    with pytest.raises(ValueError, match='callback code changed'):
        with memo.scope():
            memo.verify()
            function.__code__ = replacement.__code__
            memo.verify()


def test_foreign_thread_has_no_reuse_or_replay():
    state, memo = fixture()
    with memo.scope():
        memo.verify()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(ValueError, match='foreign'):
                executor.submit(memo.verify).result()
    assert state['calls'] == 1


@pytest.mark.parametrize('image', [bytearray(b'a'), b'a' * 65])
def test_bad_input_image_clears_scope(image):
    state, memo = fixture(input_image=lambda: image)
    with pytest.raises(ValueError, match='immutable bounded'):
        with memo.scope():
            memo.verify()
    assert state['calls'] == 0 and not memo._active


def test_original_exception_never_leaves_reusable_result():
    def fail():
        raise RuntimeError('Original failed')
    state, memo = fixture(original=fail)
    with pytest.raises(RuntimeError, match='Original failed'):
        with memo.scope():
            memo.verify()
    assert memo._result is None and not memo._active


def test_input_change_during_original_is_rejected():
    state, memo = fixture()
    def original():
        state['input'] = b'changed-during-replay'
        return state['result']
    state, memo = fixture(original=original, input_image=lambda: state['input'])
    with pytest.raises(ValueError, match='input changed'):
        with memo.scope():
            memo.verify()


def test_scope_exit_rechecks_authority_without_another_verify():
    state, memo = fixture()
    with pytest.raises(ValueError, match='authority changed'):
        with memo.scope():
            memo.verify()
            state['valid'] = False
    assert memo._result is None and not memo._active


def test_nested_scope_rejects_without_invalidating_outer_scope():
    state, memo = fixture()
    with memo.scope():
        result = memo.verify()
        with pytest.raises(ValueError, match='nested'):
            with memo.scope():
                pass
        assert memo.verify() is result
    assert state['calls'] == 1


def test_reentrant_original_cannot_replay_recursively():
    calls = []
    def original():
        calls.append(1)
        return memo.verify()
    state, memo = fixture(original=original)
    with pytest.raises(ValueError, match='Reentrant'):
        with memo.scope():
            memo.verify()
    assert calls == [1] and memo._result is None


def test_caught_original_failure_cannot_be_retried_in_scope():
    calls = []
    def original():
        calls.append(1)
        raise RuntimeError('Original failed')
    state, memo = fixture(original=original)
    with pytest.raises(ValueError, match='no retry'):
        with memo.scope():
            with pytest.raises(RuntimeError):
                memo.verify()
            memo.verify()
    assert calls == [1] and memo._result is None
