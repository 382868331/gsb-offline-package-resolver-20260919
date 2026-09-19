"""Offline dependency/feature resolver — public API.

Typical use::

    from resolver import build_registry, build_request, solve, check_lock

    registry = build_registry({"packages": [...]})
    request = build_request({"id": "root-1", ...}, registry)
    result = solve(registry, request)
    if result.status == "sat":
        lock = result.to_dict()
        check_lock(registry, request, lock).raise_if_invalid()
"""

from __future__ import annotations

from .checker import LockCheck, check_lock
from .engine import DEFAULT_NODE_BUDGET, SolveResult, solve
from .errors import (
    DuplicatePackageError,
    DuplicateRootIdError,
    DuplicateVersionError,
    InvalidInputError,
    ResolverError,
    UnknownPackageError,
)
from .intervals import intersect, make_constraint
from .model import (
    FeatureDef,
    Package,
    PackageVersion,
    Registry,
    RootRequest,
    build_registry,
    build_request,
    ensure_unique_root_ids,
)

__all__ = [
    "build_registry",
    "build_request",
    "ensure_unique_root_ids",
    "solve",
    "solve_batch",
    "check_lock",
    "LockCheck",
    "SolveResult",
    "DEFAULT_NODE_BUDGET",
    "Registry",
    "RootRequest",
    "Package",
    "PackageVersion",
    "FeatureDef",
    "make_constraint",
    "intersect",
    "ResolverError",
    "DuplicatePackageError",
    "DuplicateVersionError",
    "DuplicateRootIdError",
    "UnknownPackageError",
    "InvalidInputError",
]


def solve_batch(
    registry: Registry,
    requests: list[RootRequest],
    node_budget: int = DEFAULT_NODE_BUDGET,
) -> list[SolveResult]:
    """Solve several independently submitted roots.

    Root ids must be unique within the batch; otherwise
    :class:`DuplicateRootIdError` is raised before any search.
    """
    ensure_unique_root_ids(requests)
    return [solve(registry, req, node_budget) for req in requests]
