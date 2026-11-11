"""Tests for feature/schema-and-filing: tables, filing, validation, deadlines, list.

The current time is always passed in as ``now``. Nothing here reads the real clock.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

import claims
import db

NOW = datetime(2026, 11, 2, 9, 0, 0)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def file(conn, **overrides):
    """File a claim with sensible defaults; override any field by keyword."""
    args = {
        "passenger": "Asha Rao",
        "flight": "OA101",
        "type": "lost",
        "tier": "standard",
        "note": "",
        "now": NOW,
    }
    args.update(overrides)
    return claims.file_claim(conn, **args)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# 1. Filing a valid claim


def test_file_valid_claim_is_saved_as_open_with_deadline(conn):
    claim = file(conn, passenger="Asha Rao", flight="OA101", type="damaged", tier="gold", note="Handle broken")

    assert isinstance(claim["id"], int)
    assert claim["passenger"] == "Asha Rao"
    assert claim["flight"] == "OA101"
    assert claim["type"] == "damaged"
    assert claim["tier"] == "gold"
    assert claim["note"] == "Handle broken"
    assert claim["status"] == "open"
    assert not claim["assigned_agent"]
    assert claim["filed_at"] == NOW.isoformat()
    assert claim["deadline"]
    assert [c["id"] for c in claims.list_claims(conn)] == [claim["id"]]


# 2. Blank passenger and 3. blank flight


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_passenger_is_rejected_and_nothing_is_saved(conn, blank):
    with pytest.raises(claims.ClaimError, match="passenger"):
        file(conn, passenger=blank)

    assert count(conn, "claims") == 0
    assert count(conn, "history") == 0


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_flight_is_rejected_and_nothing_is_saved(conn, blank):
    with pytest.raises(claims.ClaimError, match="flight"):
        file(conn, flight=blank)

    assert count(conn, "claims") == 0
    assert count(conn, "history") == 0


# 4. Unknown type or tier


@pytest.mark.parametrize(
    ("field", "value"),
    [("type", "stolen"), ("tier", "platinum")],
)
def test_unknown_type_or_tier_is_rejected_and_nothing_is_saved(conn, field, value):
    with pytest.raises(claims.ClaimError, match=field):
        file(conn, **{field: value})

    assert count(conn, "claims") == 0
    assert count(conn, "history") == 0


# 5. Deadline by tier


@pytest.mark.parametrize(
    ("tier", "hours"),
    [("gold", 4), ("silver", 24), ("standard", 72)],
)
def test_deadline_is_filed_time_plus_hours_for_the_tier(conn, tier, hours):
    claim = file(conn, tier=tier)

    assert claim["deadline"] == (NOW + timedelta(hours=hours)).isoformat()


# 6. List with filters


@pytest.fixture
def three_claims(conn):
    """Three claims: gold and open, silver and open, gold and escalated. Returns their ids by name."""
    gold_open = file(conn, passenger="A", tier="gold")
    silver_open = file(conn, passenger="B", tier="silver")
    gold_escalated = file(conn, passenger="C", tier="gold")
    conn.execute("UPDATE claims SET status = 'escalated' WHERE id = ?", (gold_escalated["id"],))
    conn.commit()
    return {
        "gold_open": gold_open["id"],
        "silver_open": silver_open["id"],
        "gold_escalated": gold_escalated["id"],
    }


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, {"gold_open", "silver_open", "gold_escalated"}),
        ({"status": "open"}, {"gold_open", "silver_open"}),
        ({"status": "escalated"}, {"gold_escalated"}),
        ({"status": "closed"}, set()),
        ({"tier": "gold"}, {"gold_open", "gold_escalated"}),
        ({"tier": "silver"}, {"silver_open"}),
        ({"status": "open", "tier": "gold"}, {"gold_open"}),
    ],
    ids=[
        "no-filter",
        "status-open",
        "status-escalated",
        "status-with-no-match",
        "tier-gold",
        "tier-silver",
        "status-and-tier",
    ],
)
def test_list_claims_applies_the_filters(conn, three_claims, filters, expected):
    listed = {c["id"] for c in claims.list_claims(conn, **filters)}

    assert listed == {three_claims[name] for name in expected}


# 7. CHECK constraints in the database itself


def insert_claim_row(conn, **overrides):
    row = {
        "passenger": "Asha Rao",
        "flight": "OA101",
        "type": "lost",
        "tier": "standard",
        "status": "open",
        "note": "",
        "filed_at": NOW.isoformat(),
        "deadline": (NOW + timedelta(hours=72)).isoformat(),
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO claims (passenger, flight, type, tier, status, note, filed_at, deadline) "
        "VALUES (:passenger, :flight, :type, :tier, :status, :note, :filed_at, :deadline)",
        row,
    )


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [("type", "stolen"), ("tier", "platinum"), ("status", "lost-in-space")],
)
def test_database_check_constraint_rejects_bad_type_tier_or_status(conn, column, bad_value):
    insert_claim_row(conn)  # a valid row goes in, so the failure below is the CHECK constraint

    with pytest.raises(sqlite3.IntegrityError):
        insert_claim_row(conn, **{column: bad_value})

    assert count(conn, "claims") == 1


# Filing writes the first history entry


def test_filing_writes_the_first_history_entry(conn):
    claim = file(conn)

    rows = conn.execute(
        "SELECT claim_id, old_status, new_status, changed_by, changed_at FROM history"
    ).fetchall()
    assert len(rows) == 1
    entry = rows[0]
    assert entry["claim_id"] == claim["id"]
    assert entry["old_status"] is None
    assert entry["new_status"] == "open"
    assert entry["changed_by"]
    assert entry["changed_at"] == NOW.isoformat()


# Additional coverage for filing, history and listing


def test_filing_records_who_filed_the_claim_in_history(conn):
    file(conn, filed_by="agent.mehta")

    entry = conn.execute("SELECT changed_by FROM history").fetchone()
    assert entry["changed_by"] == "agent.mehta"


def test_each_filing_gets_its_own_id_and_its_own_history_entry(conn):
    first = file(conn, passenger="A")
    second = file(conn, passenger="B")

    assert first["id"] != second["id"]
    rows = conn.execute("SELECT claim_id, old_status, new_status FROM history ORDER BY claim_id").fetchall()
    assert [(r["claim_id"], r["old_status"], r["new_status"]) for r in rows] == [
        (first["id"], None, "open"),
        (second["id"], None, "open"),
    ]


def test_returned_claim_matches_the_saved_row(conn):
    claim = file(conn, note="Strap torn")

    assert claims.list_claims(conn) == [claim]


@pytest.mark.parametrize(
    ("field", "value"),
    [("type", "Lost"), ("tier", "GOLD")],
)
def test_type_and_tier_must_match_the_allowed_values_exactly(conn, field, value):
    with pytest.raises(claims.ClaimError, match=field):
        file(conn, **{field: value})

    assert count(conn, "claims") == 0


def test_deadline_rolls_over_into_the_next_day_and_month(conn):
    late = datetime(2026, 11, 30, 22, 0, 0)

    claim = file(conn, tier="silver", now=late)

    assert claim["filed_at"] == "2026-11-30T22:00:00"
    assert claim["deadline"] == "2026-12-01T22:00:00"


def test_list_claims_on_an_empty_database_returns_an_empty_list(conn):
    assert claims.list_claims(conn) == []


def test_list_claims_returns_claims_in_the_order_they_were_filed(conn):
    ids = [file(conn, passenger=name)["id"] for name in ("A", "B", "C")]

    assert [c["id"] for c in claims.list_claims(conn)] == ids
