"""Explicit native V4 publication context; historical admission remains closed.

Transport units, caller digests and source-equivalence replay never grant cash,
protection or installed source authority. The existing independent admission
hooks must succeed before this entry point can reach generic durable writes.
"""
from dataclasses import dataclass
from datetime import date

from .arte_declared_native_v4_unit import DeclaredNativeV4Unit, prepare_declared_native_v4_unit
from . import arte_declared_native_command_v4 as entry
from . import arte_declared_native_management_v4 as management
from .arte_declared_native_entry_schema import TABLES as ENTRY_TABLES
from .arte_declared_native_management_schema import TABLES as MANAGEMENT_TABLES


TABLE_NAMES = frozenset(table.name for table in (*ENTRY_TABLES, *MANAGEMENT_TABLES))
ENTITY_TYPES = frozenset(('declared_native_intent', 'declared_native_management_intent'))


def declared_contexts_by_batch(run_id, contexts, *, max_commits):
    """Index exact source contexts without granting source or cash admission."""
    if type(contexts) is not tuple or len(contexts) > max_commits:
        raise ValueError('Declared recovery contexts require a bounded tuple')
    result = {}
    for context in contexts:
        if type(context) is not DeclaredNativePublicationContext:
            raise ValueError('Declared recovery context has a foreign type')
        context.scope()
        base = context.unit.base
        if base.run_id != run_id or base.batch_id in result:
            raise ValueError('Declared recovery contexts have foreign or repeated batch identity')
        result[base.batch_id] = context
    return result


@dataclass(frozen=True, slots=True)
class DeclaredNativePublicationContext:
    unit: DeclaredNativeV4Unit
    resolver: object
    spec: object
    envelope: object
    approval: object
    market: object
    predecessor: object
    entry_packet: object = None
    entry_predecessor: object = None

    def scope(self):
        if type(self.unit) is not DeclaredNativeV4Unit:
            raise ValueError('Declared publication requires its exact complete unit')
        self.unit.__post_init__()
        scope = dict(resolver=self.resolver, spec=self.spec, envelope=self.envelope,
            approval=self.approval, market=self.market, predecessor=self.predecessor)
        if type(self.unit.packet) is management.DeclaredManagementRows:
            scope.update(entry_packet=self.entry_packet, entry_predecessor=self.entry_predecessor)
        elif self.entry_packet is not None or self.entry_predecessor is not None:
            raise ValueError('Declared entry publication has foreign management context')
        return scope

    def verify_admission(self):
        scope = self.scope()
        fresh = prepare_declared_native_v4_unit(self.unit.packet, **scope)
        if not entry._exact(fresh.base, self.unit.base):
            raise ValueError('Declared publication changed its original semantic batch')
        if type(self.unit.packet) is entry.DeclaredEntryRows:
            entry.verify_declared_entry_financial_admission(self.unit.packet, **scope)
            facts = entry.readback_declared_entry_source_equivalence(self.unit.packet, **scope)
            source, _, _, _ = self.resolver.reload_entry_plan()
            from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation
            from .declared_native_submission import DeclaredSubmissionBinding
            p = facts.proposal
            binding = DeclaredSubmissionBinding(
                DeclaredEntryPreparation(p.run_id, p.assignment_id, p.account_id, source),
                date.fromisoformat(self.market.sessions[0]), self.spec.execution.entry_request,
                self.envelope['payload_hash'], entry._hash(self.spec.execution.payload()))
        else:
            management.verify_declared_management_source_and_financial_admission(self.unit.packet, **scope)
            binding = management.readback_declared_management_transport(self.unit.packet, **scope).binding
        from .declared_native_submission import require_installed_submission_binding
        require_installed_submission_binding(binding)


def requires_declared_publication(families):
    return any(name in TABLE_NAMES and rows or name == 'trading_event_v1' and any(
        row.get('entity_type') in ENTITY_TYPES for row in rows) for name, rows in families)


def verify_declared_publication_graph(batch, base_families, families, context):
    """Reject missing companions and detached graph before any durable action."""
    required = requires_declared_publication(families)
    if not required and context is None:
        return
    if (not required or type(context) is not DeclaredNativePublicationContext
            or type(context.unit) is not DeclaredNativeV4Unit or context.unit.base is not batch):
        raise ValueError('Declared native publication lacks complete exact source context')
    from .arte_journal_writer import _sealed_families
    expected_base = _sealed_families(batch, declared_unit=context.unit)
    expected = (*expected_base, *context.unit.packet.families)
    if not entry._exact(base_families, expected_base) or not entry._exact(families, expected):
        raise ValueError('Declared native publication graph is incomplete or changed')
    context.verify_admission()


def verify_declared_cold_inventory(run_id, batch_id, family_rows, context):
    """Named native families cannot pass generic cold recovery unverified."""
    if context is None and not any(row.get('family_name') in TABLE_NAMES for row in family_rows):
        return
    if (type(context) is not DeclaredNativePublicationContext
            or type(context.unit) is not DeclaredNativeV4Unit
            or context.unit.base.run_id != run_id or context.unit.base.batch_id != batch_id):
        raise ValueError('Declared native cold recovery lacks complete exact source context')
    # This intentionally remains closed at independently reconstructed financial
    # and producer admission. Generic row hashes alone cannot advance recovery.
    context.verify_admission()
    expected = _declared_identities(context)
    inventory = {}
    for row in family_rows:
        name, count = row.get('family_name'), row.get('row_count')
        if type(name) is not str or type(count) is not int or name in inventory:
            raise ValueError('Declared cold inventory has duplicate or untyped families')
        inventory[name] = count
    if inventory != {name: len(rows) for name, rows in expected.items()}:
        raise ValueError('Declared cold inventory differs from independently replayed unit')


def _declared_identities(context):
    from .arte_journal_writer import _sealed_families, _identity
    families = (*_sealed_families(context.unit.base, declared_unit=context.unit),
                *context.unit.packet.families)
    return {name: _identity(rows) for name, rows in families if rows}


def verify_declared_cold_graph(details, context):
    """Bind stored, hash-verified rows to the complete original source unit."""
    if context is None:
        return
    if type(context) is not DeclaredNativePublicationContext:
        raise ValueError('Declared cold graph requires exact source context')
    context.verify_admission()
    if {name: sorted(rows) for name, rows in details.items()} != _declared_identities(context):
        raise ValueError('Declared cold graph differs from independently replayed unit')


def publish_declared_native_v4(client, context):
    """Use the shared children-before-commit path only after full admission."""
    if type(context) is not DeclaredNativePublicationContext:
        raise ValueError('Declared publication requires exact context')
    context.verify_admission()
    from .arte_journal_writer import _sealed_families
    from .arte_journal_commit_v4 import _publish_sealed_batch_v4
    base = context.unit.base
    base_families = _sealed_families(base, declared_unit=context.unit)
    return _publish_sealed_batch_v4(client, base, base_families,
        (*base_families, *context.unit.packet.families),
        verified_prior_prefix=context.predecessor.prefix,
        declared_native_context=context)
