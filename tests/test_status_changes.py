"""Tests for feature/assign-and-status: allowed changes, assign, resolve, close, history.

The current time is always passed in as ``now``. Nothing here reads the real clock.
"""

from datetime import datetime, timedelta

import pytest

import claims
import db

FILED = datetime(2026, 11, 2, 9, 0, 0)
LATER = FILED + timedelta(hours=1)

# The status table from the spec, written out as data.
ALLOWED = {
    "open": {"assigned", "escalated"},
    "assigned": {"resolved", "escalated"},
    "escalated": {"assigned", "resolved"},
    "resolved": {"closed"},
    "closed": set(),
}
STATUSES = list(ALLOWED)
ALLOWED_PAIRS = [
    pytest.param(old, new, id=f"{old}-to-{new}")
    for old, targets in ALLOWED.items()
    for new in sorted(targets)
]
DISALLOWED_PAIRS = [
    pytest.param(old, new, id=f"{old}-to-{new}")
    for old in STATUSES
    for new in STATUSES
    if new not in ALLOWED[old]
]


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def claim_in_status(conn, status, agent=None):
    """File a claim, then put it straight into the given status (and agent) for the test."""
    claim = claims.file_claim(
        conn,
        passenger="Asha Rao",
        flight="OA101",
        type="lost",
        tier="gold",
        note="Handle broken",
        now=FILED,
    )
    conn.execute(
        "UPDATE claims SET status = ?, assigned_agent = ? WHERE id = ?",
        (status, agent, claim["id"]),
    )
    conn.commit()
    return claim["id"]


def stored(conn, claim_id):
    return next(c for c in claims.list_claims(conn) if c["id"] == claim_id)


def history_count(conn, claim_id):
    return len(claims.get_history(conn, claim_id))


# 1. Every allowed change works


@pytest.mark.parametrize(("old", "new"), ALLOWED_PAIRS)
def test_allowed_change_updates_the_status_and_adds_a_history_entry(conn, old, new):
    claim_id = claim_in_status(conn, old)

    result = claims.change_status(conn, claim_id, new, changed_by="desk.lead", now=LATER)

    assert result["status"] == new
    assert stored(conn, claim_id)["status"] == new
    last = claims.get_history(conn, claim_id)[-1]
    assert (last["old_status"], last["new_status"]) == (old, new)
    assert last["changed_by"] == "desk.lead"
    assert last["changed_at"] == LATER.isoformat()


# 2. Every other change is rejected


@pytest.mark.parametrize(("old", "new"), DISALLOWED_PAIRS)
def test_disallowed_change_is_rejected_and_nothing_changes(conn, old, new):
    claim_id = claim_in_status(conn, old)

    with pytest.raises(claims.ClaimError, match=f"{old}.*{new}"):
        claims.change_status(conn, claim_id, new, changed_by="desk.lead", now=LATER)

    assert stored(conn, claim_id)["status"] == old
    assert history_count(conn, claim_id) == 1  # only the entry made when it was filed


def test_unknown_status_is_rejected_and_nothing_changes(conn):
    claim_id = claim_in_status(conn, "open")

    with pytest.raises(claims.ClaimError, match="status"):
        claims.change_status(conn, claim_id, "lost-in-space", changed_by="desk.lead", now=LATER)

    assert stored(conn, claim_id)["status"] == "open"
    assert history_count(conn, claim_id) == 1


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda c: claims.change_status(c, 999, "assigned", "desk.lead", LATER), id="change_status"),
        pytest.param(lambda c: claims.assign(c, 999, "Mehta", "desk.lead", LATER), id="assign"),
        pytest.param(lambda c: claims.resolve(c, 999, "desk.lead", LATER), id="resolve"),
        pytest.param(lambda c: claims.close(c, 999, "desk.lead", LATER), id="close"),
    ],
)
def test_unknown_claim_id_is_rejected(conn, call):
    with pytest.raises(claims.ClaimError, match="999"):
        call(conn)

    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_changed_by_is_rejected_and_nothing_changes(conn, blank):
    claim_id = claim_in_status(conn, "open")

    with pytest.raises(claims.ClaimError, match="changed_by"):
        claims.change_status(conn, claim_id, "assigned", changed_by=blank, now=LATER)

    assert stored(conn, claim_id)["status"] == "open"
    assert history_count(conn, claim_id) == 1


# 3. Assign


@pytest.mark.parametrize("start", ["open", "escalated"])
def test_assign_sets_the_agent_and_status_and_adds_a_history_entry(conn, start):
    claim_id = claim_in_status(conn, start)

    result = claims.assign(conn, claim_id, "Mehta", changed_by="desk.lead", now=LATER)

    assert result["status"] == "assigned"
    assert result["assigned_agent"] == "Mehta"
    assert stored(conn, claim_id)["assigned_agent"] == "Mehta"
    last = claims.get_history(conn, claim_id)[-1]
    assert (last["old_status"], last["new_status"]) == (start, "assigned")
    assert last["changed_by"] == "desk.lead"
    assert last["changed_at"] == LATER.isoformat()


def test_assign_trims_the_agent_name(conn):
    claim_id = claim_in_status(conn, "open")

    result = claims.assign(conn, claim_id, "  Mehta  ", changed_by="desk.lead", now=LATER)

    assert result["assigned_agent"] == "Mehta"


@pytest.mark.parametrize("blank", ["", "   "])
def test_assign_with_a_blank_agent_is_rejected_and_nothing_changes(conn, blank):
    claim_id = claim_in_status(conn, "open")

    with pytest.raises(claims.ClaimError, match="agent"):
        claims.assign(conn, claim_id, blank, changed_by="desk.lead", now=LATER)

    assert stored(conn, claim_id)["status"] == "open"
    assert not stored(conn, claim_id)["assigned_agent"]
    assert history_count(conn, claim_id) == 1


@pytest.mark.parametrize("start", ["assigned", "resolved", "closed"])
def test_assign_is_rejected_from_other_statuses_and_keeps_the_old_agent(conn, start):
    claim_id = claim_in_status(conn, start, agent="Original")

    with pytest.raises(claims.ClaimError, match=start):
        claims.assign(conn, claim_id, "Mehta", changed_by="desk.lead", now=LATER)

    assert stored(conn, claim_id)["status"] == start
    assert stored(conn, claim_id)["assigned_agent"] == "Original"
    assert history_count(conn, claim_id) == 1


# 4. Resolve


@pytest.mark.parametrize("start", ["assigned", "escalated"])
def test_resolve_works_from_assigned_and_from_escalated(conn, start):
    claim_id = claim_in_status(conn, start, agent="Mehta")

    result = claims.resolve(conn, claim_id, changed_by="Mehta", now=LATER)

    assert result["status"] == "resolved"
    last = claims.get_history(conn, claim_id)[-1]
    assert (last["old_status"], last["new_status"]) == (start, "resolved")
    assert last["changed_by"] == "Mehta"
    assert last["changed_at"] == LATER.isoformat()


@pytest.mark.parametrize("start", ["open", "resolved", "closed"])
def test_resolve_is_rejected_from_other_statuses(conn, start):
    claim_id = claim_in_status(conn, start)

    with pytest.raises(claims.ClaimError, match=start):
        claims.resolve(conn, claim_id, changed_by="Mehta", now=LATER)

    assert stored(conn, claim_id)["status"] == start
    assert history_count(conn, claim_id) == 1


def test_resolve_keeps_the_assigned_agent(conn):
    claim_id = claim_in_status(conn, "assigned", agent="Mehta")

    result = claims.resolve(conn, claim_id, changed_by="Mehta", now=LATER)

    assert result["assigned_agent"] == "Mehta"


# 5. Close


def test_close_works_from_resolved(conn):
    claim_id = claim_in_status(conn, "resolved", agent="Mehta")

    result = claims.close(conn, claim_id, changed_by="desk.lead", now=LATER)

    assert result["status"] == "closed"
    last = claims.get_history(conn, claim_id)[-1]
    assert (last["old_status"], last["new_status"]) == ("resolved", "closed")
    assert last["changed_by"] == "desk.lead"
    assert last["changed_at"] == LATER.isoformat()


@pytest.mark.parametrize("start", ["open", "assigned", "escalated", "closed"])
def test_close_is_rejected_from_every_status_except_resolved(conn, start):
    claim_id = claim_in_status(conn, start)

    with pytest.raises(claims.ClaimError, match=start):
        claims.close(conn, claim_id, changed_by="desk.lead", now=LATER)

    assert stored(conn, claim_id)["status"] == start
    assert history_count(conn, claim_id) == 1


# 6. A status change touches nothing else


def test_status_change_leaves_the_other_claim_fields_alone(conn):
    claim_id = claim_in_status(conn, "open")
    before = stored(conn, claim_id)

    claims.change_status(conn, claim_id, "escalated", changed_by="desk.lead", now=LATER)

    after = stored(conn, claim_id)
    assert {k: v for k, v in after.items() if k != "status"} == {
        k: v for k, v in before.items() if k != "status"
    }


# 7. History


def test_history_lists_the_filing_then_every_change_oldest_first(conn):
    claim = claims.file_claim(
        conn,
        passenger="Asha Rao",
        flight="OA101",
        type="lost",
        tier="standard",
        note="",
        now=FILED,
        filed_by="front.desk",
    )
    claims.assign(conn, claim["id"], "Mehta", changed_by="desk.lead", now=LATER)
    claims.resolve(conn, claim["id"], changed_by="Mehta", now=LATER + timedelta(hours=1))
    claims.close(conn, claim["id"], changed_by="desk.lead", now=LATER + timedelta(hours=2))

    entries = claims.get_history(conn, claim["id"])

    assert [(e["old_status"], e["new_status"], e["changed_by"]) for e in entries] == [
        (None, "open", "front.desk"),
        ("open", "assigned", "desk.lead"),
        ("assigned", "resolved", "Mehta"),
        ("resolved", "closed", "desk.lead"),
    ]
    assert [e["changed_at"] for e in entries] == [
        FILED.isoformat(),
        LATER.isoformat(),
        (LATER + timedelta(hours=1)).isoformat(),
        (LATER + timedelta(hours=2)).isoformat(),
    ]


def test_every_history_entry_has_who_and_when_and_a_new_status(conn):
    claim_id = claim_in_status(conn, "open")
    claims.assign(conn, claim_id, "Mehta", changed_by="desk.lead", now=LATER)

    entries = claims.get_history(conn, claim_id)

    assert len(entries) == 2
    for entry in entries:
        assert entry["claim_id"] == claim_id
        assert entry["new_status"]
        assert entry["changed_by"]
        assert entry["changed_at"]
    assert entries[1]["old_status"]  # every change after filing has an old status


def test_history_only_returns_the_entries_of_the_asked_claim(conn):
    first = claim_in_status(conn, "open")
    second = claim_in_status(conn, "open")
    claims.assign(conn, first, "Mehta", changed_by="desk.lead", now=LATER)

    assert history_count(conn, first) == 2
    assert history_count(conn, second) == 1


def test_history_of_an_unknown_claim_id_is_rejected(conn):
    with pytest.raises(claims.ClaimError, match="999"):
        claims.get_history(conn, 999)
