"""Deterministic, fair ordering for a read-only university account crawl.

The planner receives identifiers only. Credentials and browser sessions must
never be placed in queue payloads or this plan.
"""

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class CrawlCandidate:
    group_id: UUID
    user_id: UUID
    subgroup_ids: frozenset[UUID] = frozenset()


def fair_crawl_order(candidates: list[CrawlCandidate]) -> tuple[CrawlCandidate, ...]:
    """Alternate groups while covering distinct known subgroups early.

    All candidates of a group compete on the number of its still-uncovered
    subgroup IDs. Remaining ties are stable by user ID. A student with no
    confirmed subgroup runs after known subgroup representatives in that group.
    """
    grouped: dict[UUID, list[CrawlCandidate]] = defaultdict(list)
    seen_users: set[tuple[UUID, UUID]] = set()
    for candidate in candidates:
        key = (candidate.group_id, candidate.user_id)
        if key in seen_users:
            raise ValueError("duplicate_crawl_candidate")
        seen_users.add(key)
        grouped[candidate.group_id].append(candidate)

    covered: dict[UUID, set[UUID]] = defaultdict(set)
    ordered = []
    groups = sorted(grouped)
    while any(grouped.values()):
        for group_id in groups:
            remaining = grouped[group_id]
            if not remaining:
                continue
            choice = min(
                remaining,
                key=lambda row: (
                    -len(row.subgroup_ids - covered[group_id]),
                    str(row.user_id),
                ),
            )
            remaining.remove(choice)
            covered[group_id].update(choice.subgroup_ids)
            ordered.append(choice)
    return tuple(ordered)
