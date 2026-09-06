"""Trading consumers of the causal ClickHouse point-price contract."""
import unittest
from datetime import timedelta
from src.trading_runtime import strategy_engine as S
from src.trading_runtime.structure_level_contract import BOOK_VERSION, strategy_snapshot
from tests.test_long_momentum_strategy import NOW, assignment, confirmed_observation


def row(price, side=-1, score=4):
    return dict(unified_level_id=str(price), price=price, lower=price-10, upper=price+10,
                side=side, prominence=score, book_version=BOOK_VERSION,
                created_at_ms=NOW.timestamp()*1000, confirmed_at_ms=NOW.timestamp()*1000)


class PointBookTests(unittest.TestCase):
    def test_causal_threshold_and_chart_geometry_are_independent(self):
        rows=[row(101,score=s) for s in (3.99,4,5,float('nan'))]
        rows.append(dict(row(102),confirmed_at_ms=(NOW+timedelta(seconds=1)).timestamp()*1000))
        result=strategy_snapshot({'unified_levels':rows},NOW)['unified_levels']
        self.assertEqual([r['prominence'] for r in result],[4,5])
        self.assertEqual(result[0]['lower'],101)
        self.assertEqual(result[0]['band_lower'],91)
        self.assertEqual(rows[1]['lower'],91)

    def test_overlapping_bands_keep_distinct_prices_and_identity(self):
        result=S._consolidated_structure_levels([row(101),row(102),row(103,score=3)],side='long')
        self.assertEqual([r['price'] for r in result],[101,102])
        self.assertTrue(all(r['lower']==r['upper']==r['price'] for r in result))

    def test_entry_quality_requires_prominence_not_unavailable_legacy_scores(self):
        p=S.resolve_long_momentum_parameters(revision=47)
        self.assertTrue(S._level_is_entry_quality(row(101),p['structural_entry'],observed_at=NOW))
        self.assertFalse(S._level_is_entry_quality(row(101,score=3.99),p['structural_entry'],observed_at=NOW))

    def test_real_entry_intent_uses_second_support_and_third_resistance(self):
        p=S.resolve_long_momentum_parameters(revision=47)
        p['structural_entry']['enabled']=False  # Isolate downstream entry protection from timing.
        obs=confirmed_observation(price=101,bar_open=100,
            structural_support_levels=(row(99,1,3.99),row(98,1),row(97,1)),
            structural_resistance_levels=(row(101,score=3),row(102),row(103),row(104)))
        evidence={}
        self.assertEqual(S._initial_stop(obs,p,101,side='long',selection_evidence=evidence),97)
        self.assertEqual(evidence['minimum_prominence'],4)
        self.assertEqual(S._structural_profit_targets(obs,p,stop=97,side='long',luld_target=None),[104])
        result=S.LongMomentumStrategyEngine(revision=47).evaluate(assignment(strategy_revision=47,parameters=p),obs)
        self.assertEqual(len(result.evaluation.intents),1,result.evaluation.signals)
        self.assertEqual(result.evaluation.intents[0].invalidation_price,97)

    def test_support_role_cannot_be_selected_as_resistance(self):
        p=S.resolve_long_momentum_parameters(revision=47)
        obs=confirmed_observation(price=100,structural_resistance_levels=(row(101,1),row(102),row(103)))
        self.assertEqual(S._structural_profit_targets(obs,p,stop=97,side='long',luld_target=None),[])



    def test_completed_close_and_intrabar_entry_use_current_point_identity(self):
        from dataclasses import replace
        from tests.test_long_momentum_r3_acceptance import bar
        policy=S.resolve_long_momentum_parameters(revision=47)['structural_entry']
        obs=replace(bar(),structural_resistance_levels=tuple(row(p) for p in (103,102,101,104)),
                    structural_support_levels=(row(99,1),row(98,1)))
        state={}
        result=S._prior_completed_frame_resistance_trigger(obs,policy,state)
        self.assertTrue(result['passed'])
        self.assertEqual(result['threshold_price'],101)
        tick=replace(obs,observed_at=NOW+timedelta(milliseconds=250),source_timeframe='',evaluation_events=('market_data_update',))
        self.assertTrue(S._prior_completed_frame_resistance_trigger(tick,policy,state)['passed'])
        weak=replace(tick,structural_resistance_levels=tuple(dict(r,prominence=3.99) for r in tick.structural_resistance_levels))
        self.assertFalse(S._prior_completed_frame_resistance_trigger(weak,policy,state)['passed'])

    def test_trailing_support_uses_exact_price_and_current_threshold(self):
        p=S.resolve_long_momentum_parameters(revision=47)
        state=dict(active_stop=90,initial_stop=90,entry_reference_price=100,entry_at=NOW.isoformat(),high_water_price=105)
        at=NOW+timedelta(seconds=2)
        supports=tuple(dict(row(price,1,score),confirmed_at_ms=at.timestamp()*1000) for price,score in ((104,3.99),(103,4),(102,4)))
        obs=confirmed_observation(price=105,observed_at=at,structural_support_levels=supports)
        self.assertEqual(S._ratcheted_stop(obs,p,state,side='long'),102)
