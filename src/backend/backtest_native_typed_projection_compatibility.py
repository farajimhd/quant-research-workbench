"""Exact native typed projection additions removed only for legacy source proof."""
import ast
from hashlib import sha256

CURRENT_MODULE = '0d763e23a10e95f5280e68f07f8d994a6557ed6b61452efc838a85faa0d96316'
PARENT_MODULE = 'e4459c9091a5cc57f10f78226a8bdd71a4947cf0a7152aff205b887cd25c4a7b'
EDITS = (('        liquidity_source = (journal.liquidity_fade_exit_for_record(record.record_id)\n                            if kind == (\'strategy\', \'strategy_intent\') else None)\n        native_channel_source=(journal.native_channel_exit_for_record(record.record_id)\n            if kind==(\'strategy\',\'strategy_intent\') else None)\n        if native_channel_source is not None and any(source is not None for source in\n                (profit_source,confirmation_source,liquidity_source)):\n            raise RuntimeError(\'Channel intent cannot mix exit source authorities\')\n        if (kind==(\'strategy\',\'strategy_intent\')\n                and str(record.payload.get(\'reason\',\'\')).endswith(\'_native_completed_channel_exit\')\n                and native_channel_source is None):\n            raise RuntimeError(\'Channel intent lacks its complete pending source context\')\n        if kind == ("checkpoint", "market_boundary"):\n            cursor = record.entity_id\n', '        liquidity_source = (journal.liquidity_fade_exit_for_record(record.record_id)\n                            if kind == (\'strategy\', \'strategy_intent\') else None)\n        if kind == ("checkpoint", "market_boundary"):\n            cursor = record.entity_id\n'), ('            unit = V4LiquidityFadeFailureBatch(batch, failure)\n            sources[intent.intent_id] = (batch, intent)\n        if native_channel_source is not None:\n            from .backtest_native_channel_journal_projection import project_native_channel_journal_exit\n            unit=project_native_channel_journal_exit(record,native_channel_source,\n                attempt_id=attempt,batch_id=batch_id,prior_batch_id=previous,\n                source_cursor=cursor,run_month=run_month,first_price_source=first_price_source,\n                expected_config=expected_config)\n            from .backtest_native_channel_exit_unit import native_channel_exit_intent\n            intent=native_channel_exit_intent(native_channel_source[0],strategy_number=native_channel_source[3],\n                management_policy=native_channel_source[1].policy)\n            sources[intent.intent_id]=(unit.base,intent)\n        base = unit.base if not isinstance(unit, TypedJournalBatch) else unit\n        if (base.first_sequence != sequence or base.last_sequence != sequence\n', '            unit = V4LiquidityFadeFailureBatch(batch, failure)\n            sources[intent.intent_id] = (batch, intent)\n        base = unit.base if not isinstance(unit, TypedJournalBatch) else unit\n        if (base.first_sequence != sequence or base.last_sequence != sequence\n'))

def restore_native_typed_projection_parent(source):
    digest=lambda text:sha256(ast.unparse(ast.parse(text)).encode()).hexdigest()
    observed=digest(source)
    if observed==PARENT_MODULE:return source
    if observed!=CURRENT_MODULE:
        raise ValueError('Native typed projection differs from exact reviewed source delta')
    restored=source
    for current,parent in EDITS:
        if not current or restored.count(current)!=1:
            raise ValueError('Native typed projection exact delta differs')
        restored=restored.replace(current,parent,1)
    if digest(restored)!=PARENT_MODULE:
        raise ValueError('Native typed projection complete parent restoration differs')
    return restored
