"""Trust weighting v1 (plan section 6, Phase 2).

weight = role_base x acceptance_ratio (Laplace-smoothed) x recency_factor.

Every weight change is written to lesson_weight_log with a reason, so any
number on screen can be explained. The gaming note from the plan applies:
acceptance alone is farmable, so rejection history is counted symmetrically
and reviewer seniority weights the decision (the approver signs the shared
layer, and all of it is logged).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from ppm import db

ROLE_BASE = {
    "consultant": 0.50,
    "senior_consultant": 0.65,
    "associate": 0.75,
    "approver": 0.80,
    "platform_owner": 0.70,
}
RECENCY_FLOOR = 0.5


def compute_weight(role: str, accepted: int, rejected: int,
                   last_activity: date | None = None, today: date | None = None) -> float:
    """Pure function - fully unit-testable.

    Recency is 1.0 before there is any history (nobody is 'stale' before their
    first lesson) and decays towards RECENCY_FLOOR from the last shared or
    rejected lesson.
    """
    base = ROLE_BASE.get(role, 0.5)
    acceptance = (accepted + 1) / (accepted + rejected + 2)          # Laplace smoothing
    if last_activity is None:
        recency = 1.0
    else:
        today = today or datetime.now(timezone.utc).date()
        days = max(0, (today - last_activity).days)
        recency = max(RECENCY_FLOOR, math.exp(-days / 365.0))
    return round(min(1.0, max(0.0, base * acceptance * recency)), 3)


def author_stats(author_id: str) -> dict:
    with db.connection() as conn:
        return author_stats_conn(conn, author_id)


def author_stats_conn(conn, author_id: str) -> dict:
    """Same stats on a caller-provided connection (used inside action hooks
    that already hold a transaction)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN status = 'shared' THEN 1 ELSE 0 END), 0) AS accepted, "
        "COALESCE(SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected, "
        "MAX(updated_at) AS last_activity "
        "FROM lesson_learned WHERE author_id = %s",
        (author_id,),
    ).fetchone()
    accepted, rejected, last = row[0], row[1], row[2]
    return {
        "accepted": int(accepted),
        "rejected": int(rejected),
        "last_activity": last.date() if last else None,
    }


def weight_for_author(author: dict, *, extra_accepted: int = 0, extra_rejected: int = 0) -> float:
    stats = author_stats(author["person_id"])
    return compute_weight(
        author.get("role") or "consultant",
        stats["accepted"] + extra_accepted,
        stats["rejected"] + extra_rejected,
        stats["last_activity"],
    )


def log_weight_change(lesson_id: str, old_weight: float | None, new_weight: float,
                      reason: str, actor_id: str | None) -> None:
    db.execute(
        "INSERT INTO lesson_weight_log (lesson_id, old_weight, new_weight, reason, actor_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (lesson_id, old_weight, new_weight, reason, actor_id),
    )
