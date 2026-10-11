from dataclasses import replace
from types import SimpleNamespace
import unittest

from src.trading_runtime.bounded_market_read_policy import (
    BoundedMarketReadPolicy, POLICY_KEY, RULE, SOURCE_CONTRACT,
    parse_bounded_market_read_policy,
)


class BoundedMarketReadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = BoundedMarketReadPolicy(300_000, 250_000, 128_000_000, True)
        self.release = SimpleNamespace(rule_set_contracts=(RULE,),
                                       input_contracts=(SOURCE_CONTRACT,))

    def test_exact_declaration_selects_policy_and_absence_preserves_legacy(self):
        self.assertEqual(parse_bounded_market_read_policy(
            self.release, {POLICY_KEY:self.policy.payload()}), self.policy)
        self.assertIsNone(parse_bounded_market_read_policy(
            SimpleNamespace(rule_set_contracts=(), input_contracts=()), {}))

    def test_incomplete_duplicate_or_unpaired_declarations_fail(self):
        for rules, sources, policies in (
            ((), (SOURCE_CONTRACT,), {}),
            ((RULE,), (), {POLICY_KEY:self.policy.payload()}),
            ((RULE,RULE), (SOURCE_CONTRACT,), {POLICY_KEY:self.policy.payload()}),
            ((RULE,), (SOURCE_CONTRACT,), {}),
        ):
            with self.subTest(rules=rules, sources=sources):
                with self.assertRaises(ValueError):
                    parse_bounded_market_read_policy(SimpleNamespace(
                        rule_set_contracts=rules, input_contracts=sources), policies)

    def test_altered_transport_or_ordering_semantics_fail(self):
        for key in ('transport_failure', 'ordering', 'source_pins', 'byte_scope'):
            payload=self.policy.payload(); payload[key]='changed'
            with self.assertRaises(ValueError):
                parse_bounded_market_read_policy(self.release, {POLICY_KEY:payload})

    def test_invalid_limits_and_boolean_aliases_fail(self):
        for values in ({'window_ms':101}, {'window_ms':True},
                       {'max_window_rows':0}, {'max_window_bytes':True},
                       {'subdivide_oversized_windows':1}):
            with self.assertRaises(ValueError):
                replace(self.policy, **values)
