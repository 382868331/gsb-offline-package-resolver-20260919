"""Independent lock checker.

Re-derives everything from the registry and root requests: constraint
accumulation, feature fixpoint, reachable closure. A lock is accepted only
if it matches the recomputation exactly — versions satisfy all accumulated
constraints, declared features equal the propagated fixpoint, declared
dependencies equal the active dependency set, and the locked package set is
exactly the reachable closure of the roots (no missing, no unrelated).
"""

from __future__ import annotations

from .model import (
    ANY_RANGES,
    Registry,
    RegistryError,
    Request,
    format_version,
    load_registry,
    load_requests,
    parse_ranges,
    parse_version,
    ranges_contains,
    ranges_intersect,
)
from .solver import active_deps, compute_closure


def check_lock(registry, requests, lock) -> list:
    """Return a list of problems; an empty list means the lock is valid."""
    try:
        reg = registry if isinstance(registry, Registry) else load_registry(registry)
        if isinstance(requests, tuple) and all(isinstance(r, Request) for r in requests):
            reqs = requests
        else:
            reqs = load_requests(requests, reg)
    except RegistryError as exc:
        return [f"invalid registry/requests: {exc}"]

    errors = []
    if not isinstance(lock, dict):
        return ["lock must be an object"]
    root_ids = [r.id for r in reqs]
    if sorted(lock.get("roots", [])) != sorted(root_ids):
        errors.append(
            f"lock roots {lock.get('roots')!r} do not match request ids {root_ids!r}"
        )
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        return errors + ["lock must contain a 'packages' object"]

    decisions = {}
    declared_features = {}
    declared_deps = {}
    for name, entry in packages.items():
        if name not in reg.packages:
            errors.append(f"locked package {name!r} does not exist in the registry")
            continue
        if not isinstance(entry, dict):
            errors.append(f"lock entry for {name!r} must be an object")
            continue
        try:
            ver = parse_version(entry.get("version"), f"lock package {name!r}")
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        if ver not in reg.packages[name].versions:
            errors.append(
                f"lock selects {name} {format_version(ver)} "
                "which is not in the registry"
            )
            continue
        decisions[name] = ver
        declared_features[name] = entry.get("features")
        declared_deps[name] = entry.get("dependencies")
    if errors:
        return errors

    seeds = [(r.package, r.ranges, r.features, f'root "{r.id}"') for r in reqs]
    constraints, requested = compute_closure(reg, seeds, decisions)
    reachable = set(constraints)
    locked = set(decisions)

    for name in sorted(reachable - locked):
        errors.append(f"package {name!r} is required but missing from the lock")
    for name in sorted(locked - reachable):
        errors.append(f"package {name!r} is in the lock but not reachable from the roots")

    for name in sorted(reachable & locked):
        inter = ANY_RANGES
        for ranges, _source in constraints[name]:
            inter = ranges_intersect(inter, ranges)
        ver = decisions[name]
        if not ranges_contains(inter, ver):
            errors.append(
                f"{name} {format_version(ver)} violates the accumulated constraints"
            )
        vinfo = reg.packages[name].versions[ver]
        want = requested.get(name, set())
        missing = want - set(vinfo.features)
        if missing:
            errors.append(
                f"{name} {format_version(ver)} lacks requested features {sorted(missing)}"
            )
        if declared_features.get(name) != sorted(want):
            errors.append(
                f"{name}: declared features {declared_features.get(name)!r} "
                f"!= propagated fixpoint {sorted(want)!r}"
            )
        expected = sorted(
            repr((dep.package, dep.ranges, frozenset(dep.features), source))
            for dep, source in active_deps(name, vinfo, want)
        )
        actual = []
        for d in declared_deps.get(name) or []:
            try:
                actual.append(
                    repr(
                        (
                            d["package"],
                            parse_ranges(d.get("ranges"), f"lock dep of {name!r}"),
                            frozenset(d.get("features") or []),
                            d["source"],
                        )
                    )
                )
            except (RegistryError, KeyError, TypeError):
                errors.append(f"{name}: malformed dependency entry {d!r}")
        if sorted(actual) != expected:
            errors.append(
                f"{name}: declared dependencies do not match the active dependency set"
            )
    return errors
