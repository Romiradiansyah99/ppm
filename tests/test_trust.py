"""Trust weighting v1: ordering, role factor, recency decay, bounds."""

from __future__ import annotations

from datetime import date

from ppm.trust import compute_weight


def test_acceptance_ordering():
    all_accepted = compute_weight("consultant", 5, 0)
    mixed = compute_weight("consultant", 3, 3)
    all_rejected = compute_weight("consultant", 0, 5)
    assert all_accepted > mixed > all_rejected
    assert 0.0 <= all_rejected and all_accepted <= 1.0


def test_role_factor_ordering():
    assert compute_weight("associate", 2, 0) > compute_weight("senior_consultant", 2, 0)
    assert compute_weight("senior_consultant", 2, 0) > compute_weight("consultant", 2, 0)


def test_recency_decay_with_floor():
    today = date(2026, 10, 6)
    fresh = compute_weight("consultant", 2, 0, date(2026, 10, 1), today=today)
    stale = compute_weight("consultant", 2, 0, date(2023, 1, 1), today=today)
    assert fresh > stale
    # recency floors at 0.5 - history never decays to zero
    floor_bound = 0.5 * ((2 + 1) / (2 + 0 + 2)) * 0.5
    assert abs(stale - round(floor_bound, 3)) < 0.01


def test_no_history_is_not_penalised():
    # nobody is "stale" before their first lesson: recency 1.0, acceptance 0.5
    assert compute_weight("consultant", 0, 0) == 0.25
    # a rejection lowers the next weight below the pre-history value
    assert compute_weight("consultant", 0, 1, date(2026, 10, 6), today=date(2026, 10, 6)) < 0.25


def test_unknown_role_and_bounds():
    assert compute_weight("space_cadet", 0, 0) == compute_weight("consultant", 0, 0)
    for accepted in range(0, 10, 3):
        for rejected in range(0, 10, 3):
            weight = compute_weight("consultant", accepted, rejected)
            assert 0.0 <= weight <= 1.0
