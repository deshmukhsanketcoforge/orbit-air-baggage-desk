"""Tests for the report command (stretch goal 2): open and overdue counts by tier.

Definitions used by these tests:
- open: claims whose status is ``open``.
- overdue: open or assigned claims whose deadline is strictly before ``now``
  (the same claims ``escalate`` would escalate).

The current time is always passed in. Nothing here reads the real clock.
"""

from datetime import datetime, timedelta

import pytest

import claims
import db
import main

FILED = datetime(2026, 11, 2, 9, 0, 0)
AFTER_GOLD_DEADLINE = FILED + timedelta(hours=5)  # gold is due after 4 hours, silver after 24
TIERS = ("gold", "silver", "standard")
FALLBACK_CLOCK = datetime(2030, 1, 1)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def file_tier(conn, tier, status="open"):
    claim = claims.file_claim(conn, "Asha Rao", "OA101", "lost", tier, "", FILED)
    if status != "open":
        conn.execute("UPDATE claims SET status = ? WHERE id = ?", (status, claim["id"]))
        conn.commit()
    return claim["id"]


# 1. claims.report


def test_report_on_an_empty_database_has_zero_counts_for_every_tier(conn):
    result = claims.report(conn, FILED)

    assert result == {tier: {"open": 0, "overdue": 0} for tier in TIERS}


def test_report_counts_open_claims_by_tier(conn):
    file_tier(conn, "gold")
    file_tier(conn, "gold")
    file_tier(conn, "silver")

    result = claims.report(conn, FILED)

    assert [result[t]["open"] for t in TIERS] == [2, 1, 0]


def test_report_counts_only_open_status_as_open(conn):
    file_tier(conn, "gold", status="open")
    for status in ("assigned", "escalated", "resolved", "closed"):
        file_tier(conn, "gold", status=status)

    assert claims.report(conn, FILED)["gold"]["open"] == 1


def test_report_counts_overdue_claims_by_tier(conn):
    file_tier(conn, "gold")
    file_tier(conn, "gold")
    file_tier(conn, "silver")  # not yet due

    result = claims.report(conn, AFTER_GOLD_DEADLINE)

    assert [result[t]["overdue"] for t in TIERS] == [2, 0, 0]


def test_report_overdue_includes_assigned_claims(conn):
    file_tier(conn, "gold", status="assigned")

    assert claims.report(conn, AFTER_GOLD_DEADLINE)["gold"]["overdue"] == 1


@pytest.mark.parametrize("status", ["escalated", "resolved", "closed"])
def test_report_overdue_ignores_claims_that_cannot_be_escalated(conn, status):
    file_tier(conn, "gold", status=status)

    assert claims.report(conn, AFTER_GOLD_DEADLINE)["gold"]["overdue"] == 0


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        pytest.param(FILED + timedelta(hours=4, minutes=-1), 0, id="one-minute-before"),
        pytest.param(FILED + timedelta(hours=4), 0, id="exactly-at"),
        pytest.param(FILED + timedelta(hours=4, minutes=1), 1, id="one-minute-after"),
    ],
)
def test_report_follows_the_strict_deadline_rule(conn, now, expected):
    file_tier(conn, "gold")

    assert claims.report(conn, now)["gold"]["overdue"] == expected


def test_report_does_not_change_any_claim_or_history(conn):
    file_tier(conn, "gold")

    claims.report(conn, AFTER_GOLD_DEADLINE)

    assert claims.list_claims(conn)[0]["status"] == "open"
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1


# 2. the report command


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "claims.db")


@pytest.fixture
def run(db_path, capsys):
    def _run(*argv):
        code = main.main(list(argv), db_path=db_path, clock=lambda: FALLBACK_CLOCK)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def test_report_command_prints_one_line_per_tier_with_the_counts(run):
    run("--now", FILED.isoformat(), "file", "A", "OA1", "lost", "gold")
    run("--now", FILED.isoformat(), "file", "B", "OA1", "lost", "gold")
    run("--now", FILED.isoformat(), "file", "C", "OA1", "lost", "silver")

    code, out, _ = run("--now", AFTER_GOLD_DEADLINE.isoformat(), "report")

    lines = {line.split()[0]: line for line in out.strip().splitlines()}
    assert code == 0
    assert list(lines) == ["gold", "silver", "standard"]
    assert lines["gold"].split() == ["gold", "open", "2", "overdue", "2"]
    assert lines["silver"].split() == ["silver", "open", "1", "overdue", "0"]
    assert lines["standard"].split() == ["standard", "open", "0", "overdue", "0"]


def test_report_command_without_now_uses_the_clock(run):
    run("--now", FILED.isoformat(), "file", "A", "OA1", "lost", "gold")

    _, out, _ = run("report")  # the fallback clock is years later, so the claim is overdue

    assert out.splitlines()[0].split() == ["gold", "open", "1", "overdue", "1"]
