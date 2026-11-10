"""Filing and listing baggage claims."""

from datetime import timedelta

DEADLINE_HOURS = {"gold": 4, "silver": 24, "standard": 72}
CLAIM_TYPES = ("delayed", "lost", "damaged")


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
