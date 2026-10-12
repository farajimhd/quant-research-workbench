"""Bounded program OMS cold join using central normalized journal readers."""
from .qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
from src.trading_runtime.arte_oms_projection import (
    load_latest_committed_oms_groups, load_committed_oms_admission_page,
    load_committed_oms_decision_page,
)
from src.trading_runtime.columnar_strategy_release import ProgramRelease
from src.trading_runtime.program_oms_lineage import reconstruct_program_oms_lineage


async def recover_program_oms_lineages_async(reader_factory, prefix, *, release,
        account_ids, reselect, execution_policy, protection_for, outside_rth,
        maximum_groups=2000, maximum_transitions=20000, maximum_events=100000):
    """Cold join and close the owned reader before cancellation can return.

    `reselect` must replay the exact historical filled/observed state and
    certified prepared source for each intent. The callback receives normalized
    facts, not admission permission. No group is returned without reselection.
    """
    account_ids = tuple(account_ids)
    accounts = frozenset(account_ids)
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or not isinstance(release, ProgramRelease) or not accounts
            or len(accounts) != len(account_ids) or type(outside_rth) is not bool
            or any(type(account) is not str or not account for account in accounts)
            or not callable(reselect) or not callable(protection_for)
            or type(maximum_groups) is not int or not 1 <= maximum_groups <= 4096
            or type(maximum_transitions) is not int or not 500 <= maximum_transitions <= 100000
            or type(maximum_events) is not int or not 1 <= maximum_events <= 1000000):
        raise ValueError("Program OMS cold join requires bounded declared authority")

    def read():
        client = reader_factory()
        try:
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program OMS prefix moved before cold join")
            groups = load_latest_committed_oms_groups(client, prefix,
                allowed_accounts=accounts,
                strategy_identity=(release.header["strategy_id"], release.header["strategy_number"]),
                require_tactic=True, max_transitions=maximum_transitions)
            if len(groups) > maximum_groups:
                raise ValueError("Program OMS group inventory exceeds its bound")
            history = load_complete_typed_protection_history(client, prefix,
                page_size=1000, max_events=maximum_events)
            if (history.run_id != prefix.run_id or history.through_sequence != prefix.last_sequence
                    or history.committed_batch_ids != prefix.batch_ids):
                raise ValueError("Program OMS protection prefix differs")
            result = []
            if groups:
                wanted = {group.intent_record_id for group in groups}
                if None in wanted:
                    raise ValueError("Program OMS group lacks its source intent revision")
                sources = {}
                identifiers = sorted(wanted)
                for start in range(0, len(identifiers), 500):
                    chunk = tuple(identifiers[start:start + 500])
                    page = load_committed_strategy_intent_page(client, prefix,
                        record_ids=chunk, limit=len(chunk), include_source_batch=False)
                    if len(page) != len(chunk) or {row.record_id for row in page} != set(chunk):
                        raise ValueError("Program OMS source join is incomplete")
                    sources.update((row.record_id, row) for row in page)
                admissions = load_committed_oms_admission_page(client, prefix, groups,
                    max_rows=4096)
                decisions = load_committed_oms_decision_page(client, prefix, groups, admissions,
                    max_rows=4096)
                for group in groups:
                    source = sources[group.intent_record_id]
                    reservation = admissions[group.sequence]
                    proposal = reselect(group, source, reservation, history)
                    result.append(reconstruct_program_oms_lineage(group, source, history,
                        release=release, proposal=proposal, assignment_id=reservation["assignment_id"],
                        reservation=reservation, decision=decisions[group.sequence],
                        execution_policy=execution_policy,
                        protection_profile=protection_for(proposal), outside_rth=outside_rth))
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program OMS prefix moved during cold join")
            return tuple(result)
        finally:
            client.close()
    return await _owned_worker(read)
