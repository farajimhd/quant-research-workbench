"""Declared certified read buffering; never changes strategy or journal authority."""
from dataclasses import dataclass

from .journal_contract import canonical_json

RULE = 'completed-bounded-certified-market-read@1'
SOURCE_CONTRACT = 'certified-market-window-buffer@1'
POLICY_KEY = 'bounded_market_read_policy'


@dataclass(frozen=True, slots=True)
class BoundedMarketReadPolicy:
    window_ms: int
    max_window_rows: int
    max_window_bytes: int
    subdivide_oversized_windows: bool

    def __post_init__(self):
        if (type(self.window_ms) is not int or not 100 <= self.window_ms <= 57_600_000
                or self.window_ms % 100
                or type(self.max_window_rows) is not int or not 0 < self.max_window_rows < 2**32
                or type(self.max_window_bytes) is not int or not 0 < self.max_window_bytes < 2**32
                or type(self.subdivide_oversized_windows) is not bool):
            raise ValueError('Complete bounded market read limits and selection required')

    def reader_options(self):
        self.__post_init__()
        return dict(window_ms=self.window_ms, max_window_rows=self.max_window_rows,
                    max_window_bytes=self.max_window_bytes,
                    subdivide_oversized_windows=self.subdivide_oversized_windows)

    def payload(self):
        return dict(rule=RULE, source=SOURCE_CONTRACT, **self.reader_options(),
                    exposure='complete-window-before-yield',
                    ordering='session-boundary-ticker-resolution',
                    transport_failure='terminal-without-retry',
                    byte_scope='serialized-json-utf8',
                    minimum_window_ms=100, source_pins='unchanged-certified-attempts')


def parse_bounded_market_read_policy(release, policies):
    count = release.rule_set_contracts.count(RULE)
    supplied = POLICY_KEY in policies
    source_count = release.input_contracts.count(SOURCE_CONTRACT)
    if count == 0 and not supplied and source_count == 0:
        return None
    if count != 1 or not supplied or source_count != 1:
        raise ValueError('Bounded market read needs one declared rule, source and policy')
    value = policies[POLICY_KEY]
    required = {'rule', 'source', 'window_ms', 'max_window_rows', 'max_window_bytes',
                'subdivide_oversized_windows', 'exposure', 'ordering', 'transport_failure',
                'byte_scope', 'minimum_window_ms', 'source_pins'}
    if type(value) is not dict or set(value) != required:
        raise ValueError('Complete bounded market read semantics required')
    policy = BoundedMarketReadPolicy(value['window_ms'], value['max_window_rows'],
                                    value['max_window_bytes'], value['subdivide_oversized_windows'])
    if canonical_json(value) != canonical_json(policy.payload()):
        raise ValueError('Bounded market read semantics differ from declaration')
    return policy
