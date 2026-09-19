"""Exhaustive ground-truth enumerator used to cross-validate the solver.

Only feasible for tiny registries (the task caps this at <= 4 packages):
enumerates every version assignment for every package, computes the
reachable closure from the roots under that assignment, and keeps the
assignments restricted to the reachable set that are fully consistent.
"""

from __future__ import annotations

import itertools

from .model import (
    ANY_RANGES,
    Registry,
    Request,
    load_registry,
    load_requests,
    ranges_contains,
    ranges_intersect,
)
from .solver import compute_closure


def brute_force_locks(registry, requests) -> set:
    """Return the set of all valid locks as frozensets of (pkg, version)."""
    reg = registry if isinstance(registry, Registry) else load_registry(registry)
    if isinstance(requests, tuple) and all(isinstance(r, Request) for r in requests):
        reqs = requests
    else:
        reqs = load_requests(requests, reg)

    names = sorted(reg.packages)
    version_lists = [sorted(reg.packages[n].versions) for n in names]
    seeds = [(r.package, r.ranges, r.features, f'root "{r.id}"') for r in reqs]
    solutions = set()
    for combo in itertools.product(*version_lists):
        decisions = dict(zip(names, combo))
        constraints, requested = compute_closure(reg, seeds, decisions)
        ok = True
        for pkg in constraints:
            inter = ANY_RANGES
            for ranges, _source in constraints[pkg]:
                inter = ranges_intersect(inter, ranges)
            ver = decisions[pkg]
            if not ranges_contains(inter, ver):
                ok = False
                break
            if not requested.get(pkg, set()) <= set(
                reg.packages[pkg].versions[ver].features
            ):
                ok = False
                break
        if ok:
            solutions.add(frozenset((pkg, decisions[pkg]) for pkg in constraints))
    return solutions
