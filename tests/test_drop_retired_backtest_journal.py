import json

import pytest

from scripts.clickhouse.drop_retired_backtest_journal import RETIRED, retire


class Catalog:
    def __init__(self, names=RETIRED):
        self.names = set(names)
        self.drops = []

    def execute(self, sql):
        if sql.startswith("SELECT name,engine,storage_policy FROM system.tables"):
            return "\n".join(json.dumps({"name": name, "engine": "MergeTree",
                                         "storage_policy": "live_market_ssd"})
                             for name in sorted(self.names))
        if sql.startswith("DROP TABLE arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.drops.append(name)
            self.names.remove(name)
            return ""
        if sql.startswith("SELECT count() FROM system.tables"):
            name = sql.split("name='", 1)[1].split("'", 1)[0]
            return "1\n" if name in self.names else "0\n"
        raise AssertionError(sql)


def test_retirement_is_dry_run_by_default_and_exact_on_apply() -> None:
    catalog = Catalog()
    assert retire(catalog, apply=False) == RETIRED
    assert catalog.drops == []
    assert retire(catalog, apply=True) == RETIRED
    assert catalog.drops == list(RETIRED)
    assert retire(catalog, apply=True) == ()


def test_retirement_rejects_unknown_bt_table_before_any_drop() -> None:
    catalog = Catalog((*RETIRED, "bt_future_v2"))
    with pytest.raises(RuntimeError, match="Unexpected"):
        retire(catalog, apply=True)
    assert catalog.drops == []
