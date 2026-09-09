"""Canonical interview section registry.

Single source of truth for the section order and titles, shared by the docx builder
(`scripts/build_structured.py`), this package, and any downstream evaluator.

Stdlib only — `build_structured.py` imports this and must stay dependency-free.
"""

# (slug, title). Order is the interview order.
SECTIONS: list[tuple[str, str]] = [
    ("01-interview-introduction", "Interview Introduction"),
    ("02-current-environment", "Current Environment"),
    ("03-vendor-selection-decision-criteria", "Vendor Selection & Decision Criteria"),
    ("04-competitive-comparison", "Competitive Comparison"),
    ("05-implementation-integration", "Implementation & Integration"),
    ("06-cost-total-cost-of-ownership", "Cost & Total Cost of Ownership"),
    ("07-platform-satisfaction-loyalty", "Platform Satisfaction & Loyalty"),
    ("08-switching-dynamics", "Switching Dynamics"),
    ("09-interview-wrap-up", "Interview Wrap-up"),
]

TITLES: dict[str, str] = dict(SECTIONS)

# Background statements (tenure, prior employers, prior platforms) live here. They feed
# the interviewee profiles but are not themselves an evaluable subject-matter section.
PROFILE_ONLY: frozenset[str] = frozenset({"01-interview-introduction"})

# Pure sign-off pleasantries — no substantive content anywhere in the corpus.
SKIP: frozenset[str] = frozenset({"09-interview-wrap-up"})


def eval_sections() -> list[str]:
    """Sections that get a cross-interviewee question/theme pass."""
    return [slug for slug, _ in SECTIONS if slug not in PROFILE_ONLY | SKIP]


def profile_sections() -> list[str]:
    """Sections read when building an interviewee profile (everything but the sign-off)."""
    return [slug for slug, _ in SECTIONS if slug not in SKIP]


def section_number(slug: str) -> str:
    """'06-cost-total-cost-of-ownership' -> '06'. Used to build question ids."""
    return slug.split("-", 1)[0]
