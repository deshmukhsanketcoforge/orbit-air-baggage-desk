"""Filing, listing and moving baggage claims through their statuses."""

from datetime import timedelta

DEADLINE_HOURS = {"gold": 4, "silver": 24, "standard": 72}
CLAIM_TYPES = ("delayed", "lost", "damaged")

ALLOWED = {
    "open": {"assigned", "escalated"},
    "assigned": {"resolved", "escalated"},
    "escalated": {"assigned", "resolved"},
    "resolved": {"closed"},
    "closed": set(),
}


class ClaimError(ValueError):
    """Raised when a claim fails validation."""


def file_claim(conn, passenger, flight, type, tier, note, now, filed_by="system"):
    if not passenger or not passenger.strip():
        raise ClaimError("passenger must not be blank")
    if not flight or not flight.strip():
        raise ClaimError("flight must not be blank")
    if type not in CLAIM_TYPES:
        raise ClaimError(f"unknown type: {type!r}")
    if tier not in DEADLINE_HOURS:
        raise ClaimError(f"unknown tier: {tier!r}")

    filed_at = now.isoformat()
    deadline = (now + timedelta(hours=DEADLINE_HOURS[tier])).isoformat()

    with conn:  # one transaction: claim and first history entry commit together
        cursor = conn.execute(
            "INSERT INTO claims (passenger, flight, type, tier, status, note, filed_at, deadline) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?, ?)",
            (passenger.strip(), flight.strip(), type, tier, note, filed_at, deadline),
        )
        claim_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO history (claim_id, old_status, new_status, changed_by, changed_at) "
            "VALUES (?, NULL, 'open', ?, ?)",
            (claim_id, filed_by, filed_at),
        )

    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    return dict(row)


def list_claims(conn, status=None, tier=None):
    query = "SELECT * FROM claims"
    clauses, params = [], []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if tier is not None:
        clauses.append("tier = ?")
        params.append(tier)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id"
    return [dict(row) for row in conn.execute(query, params)]


def _get_claim(conn, claim_id):
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise ClaimError(f"claim {claim_id} not found")
    return row


def _move(conn, claim_id, new_status, changed_by, now, agent=None):
    """Validate, then save the claim change and its history entry in one transaction."""
    if not changed_by or not changed_by.strip():
        raise ClaimError("changed_by must not be blank")
    claim = _get_claim(conn, claim_id)
    if new_status not in ALLOWED:
        raise ClaimError(f"unknown status: {new_status!r}")
    old_status = claim["status"]
    if new_status not in ALLOWED[old_status]:
        raise ClaimError(f"cannot change status from {old_status!r} to {new_status!r}")

    with conn:
        if agent is None:
            conn.execute("UPDATE claims SET status = ? WHERE id = ?", (new_status, claim_id))
        else:
            conn.execute(
                "UPDATE claims SET status = ?, assigned_agent = ? WHERE id = ?",
                (new_status, agent, claim_id),
            )
        conn.execute(
            "INSERT INTO history (claim_id, old_status, new_status, changed_by, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (claim_id, old_status, new_status, changed_by.strip(), now.isoformat()),
        )
    return dict(_get_claim(conn, claim_id))


def change_status(conn, claim_id, new_status, changed_by, now):
    return _move(conn, claim_id, new_status, changed_by, now)


def assign(conn, claim_id, agent, changed_by, now):
    if not agent or not agent.strip():
        raise ClaimError("agent must not be blank")
    return _move(conn, claim_id, "assigned", changed_by, now, agent=agent.strip())


def resolve(conn, claim_id, changed_by, now):
    return _move(conn, claim_id, "resolved", changed_by, now)


def close(conn, claim_id, changed_by, now):
    return _move(conn, claim_id, "closed", changed_by, now)


def get_history(conn, claim_id):
    _get_claim(conn, claim_id)
    rows = conn.execute(
        "SELECT claim_id, old_status, new_status, changed_by, changed_at "
        "FROM history WHERE claim_id = ? ORDER BY id",
        (claim_id,),
    )
    return [dict(row) for row in rows]
