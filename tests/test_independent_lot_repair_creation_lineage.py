"""Metadata history only; these fixtures grant no installed source authority."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from src.trading_runtime import independent_lot_repair_creation_lineage as lineage
from src.trading_runtime.journal_contract import JournalRecord


def packet():
    at = datetime(2026, 8, 4, tzinfo=timezone.utc)
    def order(client, kind, parent=None, price=None):
        return NS(cOID=client, orderType=kind, parentId=parent, side='SELL',
                  acctId='account', ticker='TEST', auxPrice=price)
    orders = (order('target-a', 'LMT'), order('repair-a', 'STP', price=9.),
              order('original-a', 'STP', 'entry-a', 12.),
              order('original-b', 'STP', 'entry-b', 11.))
    group = NS(orders=orders, plan=NS(order_slice_ids=('a', 'a', 'a', 'b')),
               account_id='account', group_id='group',
               intent=NS(intent_id='entry', ticker='TEST', metadata={'confirmed_support_stop': 12.}),
               broker_order_request_indexes={'repair': 1, 'a': 2, 'b': 3})
    def ack(sequence, client, broker, price, action='enter_long', active=True):
        return JournalRecord(str(sequence), 'run', sequence, at+timedelta(seconds=sequence),
            at, 'protection', 'protection_change', broker, 'account',
            dict(kind='stop', phase='effective', action=action, active=active,
                 client_order_id=client, order_id=broker, price=price, intent_id='command',
                 order_group_id='group', source_intent_id='entry', strategy_id='strategy',
                 strategy_revision=1, ticker='TEST'))
    proofs = {'original': ack(2, 'original-a', 'a', 9.),
              'creation': ack(10, 'repair-a', 'repair', 9.),
              'amend': ack(20, 'original-a', 'a', 12., 'replace_protective_stop'),
              'retirement': ack(30, 'repair-a', 'repair', 9., active=False)}
    options = dict(run_id='run', strategy_id='strategy', strategy_revision=1,
                   sequence=40, boundary=at+timedelta(seconds=40))
    return group, proofs, options, ack


def test_later_amendment_does_not_add_marker_to_existing_target_or_stop():
    group, proofs, options, _ = packet()
    for order in group.orders[:2]:
        assert lineage._metadata_at_creation(group, order, {'admission': 'original'},
                                            proofs, **options) == {'admission': 'original'}


def test_prior_other_lot_marker_is_preserved_at_actual_creation():
    group, proofs, options, ack = packet()
    proofs['earlier'] = ack(8, 'original-b', 'b', 11., 'replace_protective_stop')
    assert lineage._metadata_at_creation(group, group.orders[0], {}, proofs,
                                        **options) == {'confirmed_support_stop': 11.}


def test_later_own_repair_amendment_and_retirement_do_not_redefine_creation():
    group, proofs, options, ack = packet()
    group.orders[1].auxPrice = 12.
    proofs['repair_amend'] = ack(21, 'repair-a', 'repair', 12., 'replace_protective_stop')
    proofs['retirement'] = ack(30, 'repair-a', 'repair', 12., active=False)
    assert lineage._metadata_at_creation(group, group.orders[1], {}, proofs, **options) == {}


def test_pending_repair_requires_original_lot_acknowledgement_and_earned_price():
    group, proofs, options, _ = packet()
    del group.broker_order_request_indexes['repair']
    proofs = {k: v for k, v in proofs.items() if k not in {'creation', 'retirement'}}
    group.orders[1].auxPrice = 12.
    assert lineage._metadata_at_creation(group, group.orders[0], {}, proofs,
                                        **options) == {'confirmed_support_stop': 12.}
    group.orders[1].auxPrice = 9.
    with pytest.raises(ValueError, match='acknowledged lot price'):
        lineage._metadata_at_creation(group, group.orders[0], {}, proofs, **options)
    del proofs['original']
    with pytest.raises(ValueError, match='original lot acknowledgement'):
        lineage._metadata_at_creation(group, group.orders[0], {}, proofs, **options)


@pytest.mark.parametrize('change', ['run', 'account', 'sequence', 'time', 'strategy',
                                  'revision', 'group', 'intent', 'ticker', 'broker',
                                  'client', 'price', 'phase', 'action'])
def test_foreign_future_or_unacknowledged_history_is_rejected(change):
    group, proofs, options, _ = packet()
    record = proofs['creation']
    if change == 'run': bad = replace(record, run_id='foreign')
    elif change == 'account': bad = replace(record, account_id='foreign')
    elif change == 'sequence': bad = replace(record, sequence=options['sequence'])
    elif change == 'time': bad = replace(record, event_time=options['boundary']+timedelta(seconds=1))
    else:
        key = {'strategy': 'strategy_id', 'revision': 'strategy_revision', 'group': 'order_group_id',
               'intent': 'source_intent_id', 'ticker': 'ticker', 'broker': 'order_id',
               'client': 'client_order_id', 'price': 'price', 'phase': 'phase', 'action': 'action'}[change]
        bad = replace(record, payload={**record.payload, key: True if change == 'price' else 'foreign'})
    proofs['creation'] = bad
    with pytest.raises(ValueError):
        lineage._metadata_at_creation(group, group.orders[0], {}, proofs, **options)


def test_missing_creation_ambiguous_sequence_and_unearned_group_marker_rejected():
    group, proofs, options, _ = packet()
    no_creation = {k: v for k, v in proofs.items() if k not in {'creation', 'retirement'}}
    with pytest.raises(ValueError, match='creation acknowledgement'):
        lineage._metadata_at_creation(group, group.orders[0], {}, no_creation, **options)
    proofs['ambiguous'] = replace(proofs['creation'], payload={**proofs['creation'].payload, 'price': 8.})
    with pytest.raises(ValueError, match='ambiguous source sequence'):
        lineage._metadata_at_creation(group, group.orders[0], {}, proofs, **options)
    del proofs['ambiguous']; group.intent.metadata['confirmed_support_stop'] = 13.
    with pytest.raises(ValueError, match='group marker'):
        lineage._metadata_at_creation(group, group.orders[0], {}, proofs, **options)


def test_undeclared_source_cannot_select_new_behavior():
    group, proofs, options, _ = packet()
    assert lineage.metadata_at_creation(group, group.orders[0], {}, proofs,
                                        source=None, **options) is None


def test_exact_optional_rule_selection_and_duplicate_rejection(monkeypatch):
    from src.trading_runtime import independent_lot_initial_stop_lineage as initial
    # Installed ownership is tested by the existing native source contracts;
    # this seam owns only the additional declaration gate.
    monkeypatch.setattr(initial, 'selected_source', lambda *a, **k: True)
    group, proofs, options, _ = packet()
    rules = []
    source = NS(installed_payload={'strategy': {'numbered_release': {
        'contract': {'rule_set_contracts': rules}}}})
    assert lineage.metadata_at_creation(group, group.orders[0], {}, proofs,
                                        source=source, **options) is None
    rules.append(lineage.RULE)
    assert lineage.metadata_at_creation(group, group.orders[0], {}, proofs,
                                        source=source, **options) == {}
    rules.append(lineage.RULE)
    with pytest.raises(ValueError, match='one declared rule'):
        lineage.metadata_at_creation(group, group.orders[0], {}, proofs,
                                     source=source, **options)


def test_boolean_strategy_revision_cannot_spoof_integer_identity():
    group, proofs, options, _ = packet()
    original = proofs['creation']
    proofs['creation'] = replace(original, payload={**original.payload, 'strategy_revision': True})
    with pytest.raises(ValueError):
        lineage._metadata_at_creation(group, group.orders[0], {}, proofs, **options)
