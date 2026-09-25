from copy import deepcopy

import pytest

from src.backend.live_assignment_activation_join import (
    PinnedAssignmentMember, audit_assignment_activation_join,
)
from src.backend.live_plan_membership import (
    MEMBER, PARENT, WATCH, ZERO_HASH, ManagedPlanMembershipHeadReader,
    PlanWatchMember, TypedPlanMembershipAuthority,
    project_plan_membership,
    recover_attested_plan_membership,
)
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo


CONFIG = "config-1"
SESSION = "2026-09-24"


def _fixture():
    members = (
        PinnedAssignmentMember("a-1", "plan-1", 1, "a" * 64),
        PinnedAssignmentMember("z-2", "plan-2", 2, "b" * 64),
    )
    watches = (PlanWatchMember("plan-1", "ABC", "profile-1", "default"),)
    parent, children, watch_rows = project_plan_membership(
        members, watches, configuration_revision_id=CONFIG,
        configuration_content_hash="c" * 64, session_key=SESSION,
        source_cursor_commit_hash="d" * 64,
        membership_sequence=1, previous_hash=ZERO_HASH)
    return parent, children, watch_rows, members, watches


class Rows:
    def __init__(self, parent, children, watches):
        self.parents = [deepcopy(parent)]
        self.children = {1: list(map(deepcopy, children))}
        self.watches = {1: list(map(deepcopy, watches))}
    def read_revisions(self, *, configuration_revision_id, session_key, limit):
        assert configuration_revision_id == CONFIG and session_key == SESSION
        return deepcopy(self.parents[:limit])
    def read_members(self, *, configuration_revision_id, session_key,
                     membership_sequence, limit):
        return deepcopy(self.children[membership_sequence][:limit])
    def read_watches(self, *, configuration_revision_id, session_key,
                     membership_sequence, limit):
        return deepcopy(self.watches[membership_sequence][:limit])


class Keeper:
    def __init__(self, parent):
        self.head = (1, parent["content_hash"], 0)
    def read_head(self, *, configuration_revision_id, session_key):
        return self.head


def _recover(rows, keeper):
    return recover_attested_plan_membership(
        rows, keeper, configuration_revision_id=CONFIG,
        configuration_content_hash="c" * 64, session_key=SESSION,
        source_cursor_commit_hash="d" * 64)


def test_compact_full_roster_membership_roundtrip_and_storage_policy():
    parent, children, watch_rows, members, watches = _fixture()
    assert {name for name, _ in MEMBER.columns} == {
        "schema_version", "configuration_revision_id", "session_key",
        "membership_sequence", "assignment_id", "run_plan_id",
        "base_sequence", "base_hash", "content_hash"}
    assert "ticker" not in dict(MEMBER.columns)
    assert "profile_id" not in dict(MEMBER.columns)
    assert "book_id" not in dict(MEMBER.columns)
    assert {name for name, _ in WATCH.columns} >= {
        "run_plan_id", "ticker", "profile_id", "book_id"}
    assert all("storage_policy = 'live_market_ssd'" in table.ddl()
               for table in (PARENT, MEMBER, WATCH))
    restored = _recover(Rows(parent, children, watch_rows), Keeper(parent))
    assert restored.assignments == members and restored.watches == watches


@pytest.mark.parametrize("mutation", ["extra", "missing", "child_hash", "wrong_plan",
                                      "missing_watch", "extra_watch", "watch_hash"])
def test_membership_cold_read_rejects_orphan_gap_and_content(mutation):
    parent, children, watch_rows, _, _ = _fixture()
    rows = Rows(parent, children, watch_rows)
    if mutation == "extra":
        rows.parents.append(deepcopy(parent))
    elif mutation == "missing":
        rows.children[1].pop()
    elif mutation == "child_hash":
        rows.children[1][0]["content_hash"] = "e" * 64
    else:
        if mutation == "wrong_plan":
            rows.children[1][0]["run_plan_id"] = "other"
        elif mutation == "missing_watch":
            rows.watches[1].clear()
        elif mutation == "extra_watch":
            rows.watches[1].append(dict(rows.watches[1][0], ticker="XYZ"))
        else:
            rows.watches[1][0]["content_hash"] = "e" * 64
    with pytest.raises(ValueError):
        _recover(rows, Keeper(parent))


def test_membership_rejects_deployment_id_as_authority_and_unpinned_source():
    _, _, _, members, watches = _fixture()
    with pytest.raises(ValueError):
        project_plan_membership(
            members, watches, configuration_revision_id=CONFIG,
            configuration_content_hash="c" * 64, session_key=SESSION,
            source_cursor_commit_hash="unverified",
            membership_sequence=1, previous_hash=ZERO_HASH)
    # There is deliberately no deployment_id field in either typed table.
    assert "deployment_id" not in dict(PARENT.columns)
    assert "deployment_id" not in dict(MEMBER.columns)


def test_managed_keeper_membership_head_exact_wire_and_session():
    parent, children, watch_rows, members, watches = _fixture()
    client = FakeKazoo()
    client.add_listener = lambda _listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    reader = ManagedPlanMembershipHeadReader(session)
    path = reader.path(CONFIG, SESSION)
    with pytest.raises(ValueError, match="missing or corrupt"):
        reader.read_head(configuration_revision_id=CONFIG, session_key=SESSION)
    client.ensure_path(path.rsplit("/", 1)[0])
    client.create(path, f"1\n{CONFIG}\n{SESSION}\n1\n{parent['content_hash']}".encode())
    recovered = recover_attested_plan_membership(
        Rows(parent, children, watch_rows), reader, configuration_revision_id=CONFIG,
        configuration_content_hash="c" * 64, session_key=SESSION,
        source_cursor_commit_hash="d" * 64)
    assert recovered.assignments == members and recovered.watches == watches
    session._on_state("SUSPENDED")
    with pytest.raises(RuntimeError, match="unavailable"):
        reader.read_head(configuration_revision_id=CONFIG, session_key=SESSION)


def test_membership_chain_allows_new_source_cursor_without_rewriting_prior_revision():
    first, first_children, first_watches, members, watches = _fixture()
    second, second_children, second_watches = project_plan_membership(
        members, watches, configuration_revision_id=CONFIG,
        configuration_content_hash="c" * 64, session_key=SESSION,
        source_cursor_commit_hash="e" * 64,
        membership_sequence=2, previous_hash=first["content_hash"])
    rows = Rows(first, first_children, first_watches)
    rows.parents.append(second)
    rows.children[2] = list(second_children)
    rows.watches[2] = list(second_watches)
    keeper = Keeper(second)
    keeper.head = (2, second["content_hash"], 1)
    assert recover_attested_plan_membership(
        rows, keeper, configuration_revision_id=CONFIG,
        configuration_content_hash="c" * 64, session_key=SESSION,
        source_cursor_commit_hash="e" * 64).assignments == members


def test_selected_plan_adapter_uses_stored_watch_rows_not_observed_activations():
    parent, children, watch_rows, members, _ = _fixture()
    authority = TypedPlanMembershipAuthority(
        Rows(parent, children, watch_rows), Keeper(parent),
        configuration_content_hash="c" * 64, session_key=SESSION,
        source_cursor_commit_hash="d" * 64)
    selected = authority.read_attested_plan(
        configuration_revision_id=CONFIG, run_plan_id="plan-1")
    assert selected.assignments == members
    assert [(w.ticker, w.profile_id, w.book_id) for w in selected.activated_watches] == [
        ("ABC", "profile-1", "default")]


def test_stored_watch_expected_but_missing_activation_fails_join():
    from src.backend.live_assignment_roster import (
        RosterChange, RosterHead, project_roster_revision,
    )
    from tests.test_live_assignment_activation_join import Assignment, BaseHead
    parent, children, watch_rows, _, _ = _fixture()
    authority = TypedPlanMembershipAuthority(
        Rows(parent, children, watch_rows), Keeper(parent),
        configuration_content_hash="c" * 64, session_key=SESSION,
        source_cursor_commit_hash="d" * 64)
    roster_parent, roster_children = project_roster_revision(
        [RosterChange("a-1", "upsert", 1, "a" * 64),
         RosterChange("z-2", "upsert", 2, "b" * 64)],
        sequence=1, previous_hash=ZERO_HASH)
    class Roster:
        def read_revisions(self, *, limit):
            return [dict(roster_parent)]
        def read_changes(self, sequence, *, limit):
            return list(map(dict, roster_children))
    class Heads:
        def read_head(self, assignment_id=None):
            if assignment_id is None:
                return RosterHead(1, roster_parent["content_hash"], 0)
            return BaseHead(1, "a" * 64) if assignment_id == "a-1" else BaseHead(2, "b" * 64)
    assignments = {
        "a-1": Assignment("a-1", "ABC", {
            "campaign_profile_id": "profile-1", "campaign_book_id": "default"}),
        "z-2": Assignment("z-2", "XYZ", {
            "campaign_profile_id": "profile-2", "campaign_book_id": "other"}),
    }
    with pytest.raises(ValueError, match="coverage is incomplete"):
        audit_assignment_activation_join(
            [], Roster(), Heads(), Heads(), assignments.__getitem__,
            configuration_revision_id=CONFIG, run_plan_id="plan-1",
            membership_authority=authority)
