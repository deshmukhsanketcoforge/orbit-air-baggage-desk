"""Tests for feature/deadlines-and-escalation: the overdue rule and escalation.

The current time is always passed in as ``now``. Nothing here reads the real clock.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

import claims
import db

FILED = datetime(2026, 11, 2, 9, 0, 0)
GOLD_DEADLINE = FILED + timedelta(hours=4)  # gold gets 4 hours
ONE_MINUTE = timedelta(minutes=1)
PAST_DEADLINE = GOLD_DEADLINE + ONE_MINUTE
ESCALATABLE = ["open", "assigned"]
LEFT_ALONE = ["escalated", "resolved", "closed"]


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def file_in(conn, status, tier="gold", agent=None):
    """File a claim at FILED, then put it straight into the given status for the test."""
    claim = claims.file_claim(
        conn,
        passenger="Asha Rao",
        flight="OA101",
        type="lost",
        tier=tier,
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


# 1. The overdue rule: one minute before, exactly at, one minute after


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        pytest.param(GOLD_DEADLINE - ONE_MINUTE, False, id="one-minute-before"),
        pytest.param(GOLD_DEADLINE, False, id="exactly-at-the-deadline"),
        pytest.param(GOLD_DEADLINE + ONE_MINUTE, True, id="one-minute-after"),
    ],
)
def test_is_overdue_only_when_now_is_strictly_later_than_the_deadline(conn, now, expected):
    claim = claims.file_claim(conn, "Asha Rao", "OA101", "lost", "gold", "", FILED)

    assert claims.is_overdue(claim, now) is expected


def test_is_overdue_uses_the_deadline_of_the_claims_own_tier(conn):
    gold = claims.file_claim(conn, "Asha Rao", "OA101", "lost", "gold", "", FILED)
    silver = claims.file_claim(conn, "Ben Cole", "OA102", "lost", "silver", "", FILED)
    five_hours_later = FILED + timedelta(hours=5)

    assert claims.is_overdue(gold, five_hours_later) is True
    assert claims.is_overdue(silver, five_hours_later) is False


# 2. Escalation moves overdue open and assigned claims


@pytest.mark.parametrize("start", ESCALATABLE)
def test_escalate_overdue_escalates_an_overdue_claim_and_returns_its_id(conn, start):
    claim_id = file_in(conn, start, agent="Mehta" if start == "assigned" else None)

    escalated = claims.escalate_overdue(conn, now=PAST_DEADLINE)

    assert escalated == [claim_id]
    assert stored(conn, claim_id)["status"] == "escalated"


# 3. Everything else is left alone


@pytest.mark.parametrize("start", LEFT_ALONE)
def test_escalate_overdue_leaves_resolved_closed_and_escalated_claims_alone(conn, start):
    claim_id = file_in(conn, start, agent="Mehta")

    escalated = claims.escalate_overdue(conn, now=PAST_DEADLINE)

    assert escalated == []
    assert stored(conn, claim_id)["status"] == start
    assert history_count(conn, claim_id) == 1  # only the entry made when it was filed


@pytest.mark.parametrize("start", ESCALATABLE)
@pytest.mark.parametrize("now", [GOLD_DEADLINE - ONE_MINUTE, GOLD_DEADLINE])
def test_escalate_overdue_leaves_claims_that_are_not_yet_overdue_alone(conn, start, now):
    claim_id = file_in(conn, start)

    escalated = claims.escalate_overdue(conn, now=now)

    assert escalated == []
    assert stored(conn, claim_id)["status"] == start
    assert history_count(conn, claim_id) == 1


def test_escalate_overdue_with_nothing_to_do_returns_an_empty_list(conn):
    assert claims.escalate_overdue(conn, now=PAST_DEADLINE) == []
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0


def test_escalate_overdue_escalates_only_the_overdue_claims_and_returns_their_ids(conn):
    # The assigned claim is filed first, so id order differs from status order.
    overdue_assigned = file_in(conn, "assigned", agent="Mehta")
    overdue_open = file_in(conn, "open")
    not_overdue = file_in(conn, "open", tier="silver")  # 24 hours, so still on time
    resolved = file_in(conn, "resolved", agent="Mehta")

    escalated = claims.escalate_overdue(conn, now=PAST_DEADLINE)

    assert escalated == [overdue_assigned, overdue_open]
    assert stored(conn, overdue_open)["status"] == "escalated"
    assert stored(conn, overdue_assigned)["status"] == "escalated"
    assert stored(conn, not_overdue)["status"] == "open"
    assert stored(conn, resolved)["status"] == "resolved"


# 4. Escalation writes history


@pytest.mark.parametrize("start", ESCALATABLE)
def test_escalation_adds_a_history_entry_for_each_escalated_claim(conn, start):
    claim_id = file_in(conn, start)

    claims.escalate_overdue(conn, now=PAST_DEADLINE, changed_by="desk.lead")

    entries = claims.get_history(conn, claim_id)
    assert len(entries) == 2
    last = entries[-1]
    assert (last["old_status"], last["new_status"]) == (start, "escalated")
    assert last["changed_by"] == "desk.lead"
    assert last["changed_at"] == PAST_DEADLINE.isoformat()


def test_escalation_is_recorded_as_made_by_system_unless_someone_is_named(conn):
    claim_id = file_in(conn, "open")

    claims.escalate_overdue(conn, now=PAST_DEADLINE)

    assert claims.get_history(conn, claim_id)[-1]["changed_by"] == "system"


def test_escalation_keeps_the_assigned_agent_and_the_other_claim_fields(conn):
    claim_id = file_in(conn, "assigned", agent="Mehta")
    before = stored(conn, claim_id)

    claims.escalate_overdue(conn, now=PAST_DEADLINE)

    after = stored(conn, claim_id)
    assert {k: v for k, v in after.items() if k != "status"} == {
        k: v for k, v in before.items() if k != "status"
    }


# 5. Rollback: if the history entry cannot be saved, the claim must not change


@pytest.mark.parametrize("start", ESCALATABLE)
def test_escalation_changes_nothing_when_the_history_entry_cannot_be_saved(conn, start):
    claim_id = file_in(conn, start)
    # Make every history insert for an escalation fail on purpose.
    conn.execute(
        "CREATE TRIGGER block_escalation_history BEFORE INSERT ON history "
        "WHEN NEW.new_status = 'escalated' "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    conn.commit()

    with pytest.raises((sqlite3.Error, claims.ClaimError)):
        claims.escalate_overdue(conn, now=PAST_DEADLINE)

    assert stored(conn, claim_id)["status"] == start
    assert history_count(conn, claim_id) == 1


def test_escalation_is_all_or_nothing_when_one_history_entry_cannot_be_saved(conn):
    first = file_in(conn, "open")
    second = file_in(conn, "assigned", agent="Mehta")
    # Only the second claim's escalation history entry is made to fail.
    conn.execute(
        "CREATE TRIGGER block_second_escalation_history BEFORE INSERT ON history "
        f"WHEN NEW.new_status = 'escalated' AND NEW.claim_id = {second} "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    conn.commit()

    with pytest.raises((sqlite3.Error, claims.ClaimError)):
        claims.escalate_overdue(conn, now=PAST_DEADLINE)

    # The first claim must not stay escalated either: the whole run is one transaction.
    assert stored(conn, first)["status"] == "open"
    assert stored(conn, second)["status"] == "assigned"
    assert history_count(conn, first) == 1
    assert history_count(conn, second) == 1


# 6. Inputs and repeat runs


@pytest.mark.parametrize("blank", ["", "   "])
def test_escalation_with_a_blank_changed_by_is_rejected_and_nothing_changes(conn, blank):
    claim_id = file_in(conn, "open")

    with pytest.raises(claims.ClaimError, match="changed_by"):
        claims.escalate_overdue(conn, now=PAST_DEADLINE, changed_by=blank)

    assert stored(conn, claim_id)["status"] == "open"
    assert history_count(conn, claim_id) == 1


def test_escalating_twice_escalates_and_records_nothing_the_second_time(conn):
    claim_id = file_in(conn, "open")

    first = claims.escalate_overdue(conn, now=PAST_DEADLINE)
    second = claims.escalate_overdue(conn, now=PAST_DEADLINE + timedelta(hours=1))

    assert first == [claim_id]
    assert second == []
    assert history_count(conn, claim_id) == 2  # filing and the one escalation


@pytest.mark.parametrize(("tier", "hours"), [("silver", 24), ("standard", 72)])
def test_escalation_uses_each_tiers_own_deadline(conn, tier, hours):
    claim_id = file_in(conn, "open", tier=tier)
    deadline = FILED + timedelta(hours=hours)

    assert claims.escalate_overdue(conn, now=deadline) == []  # exactly at the deadline
    assert stored(conn, claim_id)["status"] == "open"

    assert claims.escalate_overdue(conn, now=deadline + ONE_MINUTE) == [claim_id]
    assert stored(conn, claim_id)["status"] == "escalated"
