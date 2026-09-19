"""Deterministic backtracking solver with feature fixpoint propagation.

Search rules (per task spec):
  * after propagation, pick the pending package with the smallest name;
  * try its versions in descending order, one node budget unit per attempt;
  * on conflict, backtrack and undo the branch's feature/dependency effects;
  * return the first solution found in this deterministic order.

The node budget is shared by the whole request. `unknown` is returned when
the budget runs out; `unsat` only when every branch has been fully excluded.
"""

from __future__ import annotations

from .model import (
    ANY_RANGES,
    Registry,
    Request,
    format_ranges,
    format_version,
    load_registry,
    load_requests,
    ranges_contains,
    ranges_empty,
    ranges_intersect,
)

_UNKNOWN = object()

MAX_KEPT_CONFLICTS = 200


def active_deps(pkg: str, vinfo, enabled_features) -> list:
    """Dependencies active for a decided package version.

    Returns (Dep, source_label) pairs: required deps plus the deps of every
    enabled feature. Source labels identify where each dependency came from.
    """
    label = f"{pkg} {format_version(vinfo.version)}"
    out = [(dep, f"{label} (required)") for dep in vinfo.deps]
    for feat in sorted(enabled_features):
        for dep in vinfo.features.get(feat, ()):
            out.append((dep, f'{label} feature "{feat}"'))
    return out


def compute_closure(registry: Registry, seeds, decisions: dict):
    """Propagate constraints and requested features to a fixpoint.

    seeds: iterable of (package, ranges, features, source) — typically the
    root requests. decisions maps package -> chosen version; only decided
    packages expand their active dependencies. Feature requests may target
    already-decided packages and enable further features there, so this
    loops until nothing changes (cycles are allowed and terminate because
    all involved sets are finite).

    Returns (constraints, requested):
      constraints: dict[pkg, list[(ranges, source)]]
      requested:   dict[pkg, set[feature names]]
    """
    constraints: dict = {}
    requested: dict = {}

    def add_constraint(pkg, ranges, source):
        lst = constraints.setdefault(pkg, [])
        if any(r == ranges and s == source for r, s in lst):
            return False
        lst.append((ranges, source))
        return True

    def add_requested(pkg, feats):
        s = requested.setdefault(pkg, set())
        before = len(s)
        s |= set(feats)
        return len(s) != before

    for pkg, ranges, feats, source in seeds:
        add_constraint(pkg, ranges, source)
        add_requested(pkg, feats)

    changed = True
    while changed:
        changed = False
        for pkg in sorted(constraints):
            if pkg not in decisions:
                continue
            vinfo = registry.packages[pkg].versions[decisions[pkg]]
            for dep, source in active_deps(pkg, vinfo, requested.get(pkg, ())):
                if add_constraint(dep.package, dep.ranges, source):
                    changed = True
                if add_requested(dep.package, dep.features):
                    changed = True
    return constraints, requested


class _Solver:
    def __init__(self, registry: Registry, requests: tuple, budget: int):
        self.registry = registry
        self.requests = requests
        self.budget_left = max(0, int(budget))
        self.nodes_used = 0
        self.constraints: dict = {}
        self.requested: dict = {}
        self.decided: dict = {}
        self.conflicts: list = []
        for req in requests:
            self._add_constraint(req.package, req.ranges, f'root "{req.id}"')
            self._add_requested(req.package, req.features)

    # --- state helpers -------------------------------------------------

    def _add_constraint(self, pkg, ranges, source) -> bool:
        lst = self.constraints.setdefault(pkg, [])
        if any(r == ranges and s == source for r, s in lst):
            return False
        lst.append((ranges, source))
        return True

    def _add_requested(self, pkg, feats) -> bool:
        s = self.requested.setdefault(pkg, set())
        before = len(s)
        s |= set(feats)
        return len(s) != before

    def _snapshot(self):
        return (
            {k: list(v) for k, v in self.constraints.items()},
            {k: set(s) for k, s in self.requested.items()},
            dict(self.decided),
        )

    def _restore(self, snapshot):
        constraints, requested, decided = snapshot
        self.constraints = {k: list(v) for k, v in constraints.items()}
        self.requested = {k: set(s) for k, s in requested.items()}
        self.decided = dict(decided)

    # --- propagation and feasibility ------------------------------------

    def _propagate(self):
        changed = True
        while changed:
            changed = False
            for pkg in sorted(self.decided):
                vinfo = self.registry.packages[pkg].versions[self.decided[pkg]]
                for dep, source in active_deps(
                    pkg, vinfo, self.requested.get(pkg, ())
                ):
                    if self._add_constraint(dep.package, dep.ranges, source):
                        changed = True
                    if self._add_requested(dep.package, dep.features):
                        changed = True

    def _intersection(self, pkg):
        result = ANY_RANGES
        for ranges, _source in self.constraints.get(pkg, ()):
            result = ranges_intersect(result, ranges)
        return result

    def _conflict(self, pkg, reason, sources) -> bool:
        self.conflicts.append(
            {
                "package": pkg,
                "reason": reason,
                "constraint_sources": list(sources),
            }
        )
        if len(self.conflicts) > MAX_KEPT_CONFLICTS:
            self.conflicts = self.conflicts[-MAX_KEPT_CONFLICTS:]
        return False

    def _check(self) -> bool:
        """Feasibility of the current (partial) state after propagation."""
        for pkg in sorted(self.constraints):
            inter = self._intersection(pkg)
            sources = [s for _, s in self.constraints[pkg]]
            if ranges_empty(inter):
                return self._conflict(pkg, "constraint intersection is empty", sources)
            pinfo = self.registry.packages[pkg]
            req = self.requested.get(pkg, set())
            fitting = [v for v in pinfo.versions if ranges_contains(inter, v)]
            if not fitting:
                return self._conflict(
                    pkg, "no registered version satisfies the combined ranges", sources
                )
            if pkg in self.decided:
                v = self.decided[pkg]
                if not ranges_contains(inter, v):
                    return self._conflict(
                        pkg,
                        f"decided version {format_version(v)} violates later constraints",
                        sources,
                    )
                missing = req - set(pinfo.versions[v].features)
                if missing:
                    return self._conflict(
                        pkg,
                        f"decided version {format_version(v)} lacks requested "
                        f"features {sorted(missing)}",
                        sources,
                    )
            else:
                if not any(req <= set(pinfo.versions[v].features) for v in fitting):
                    return self._conflict(
                        pkg,
                        f"no fitting version provides requested features {sorted(req)}",
                        sources,
                    )
        return True

    # --- search ----------------------------------------------------------

    def _search(self):
        """Returns a lock dict, None (branch fully excluded), or _UNKNOWN."""
        self._propagate()
        if not self._check():
            return None
        pending = [p for p in self.constraints if p not in self.decided]
        if not pending:
            return self._build_lock()
        pkg = min(pending)
        inter = self._intersection(pkg)
        req = self.requested.get(pkg, set())
        pinfo = self.registry.packages[pkg]
        candidates = [
            v
            for v in sorted(pinfo.versions, reverse=True)
            if ranges_contains(inter, v) and req <= set(pinfo.versions[v].features)
        ]
        for v in candidates:
            if self.budget_left <= 0:
                return _UNKNOWN
            self.budget_left -= 1
            self.nodes_used += 1
            snapshot = self._snapshot()
            self.decided[pkg] = v
            result = self._search()
            if result is _UNKNOWN:
                return _UNKNOWN
            self._restore(snapshot)
            if result is not None:
                return result
        return None

    def _build_lock(self):
        packages = {}
        for pkg in sorted(self.decided):
            vinfo = self.registry.packages[pkg].versions[self.decided[pkg]]
            deps = [
                {
                    "package": dep.package,
                    "ranges": format_ranges(dep.ranges),
                    "features": sorted(dep.features),
                    "source": source,
                }
                for dep, source in active_deps(
                    pkg, vinfo, self.requested.get(pkg, ())
                )
            ]
            packages[pkg] = {
                "version": list(self.decided[pkg]),
                "features": sorted(self.requested.get(pkg, ())),
                "dependencies": deps,
            }
        return {"roots": [req.id for req in self.requests], "packages": packages}


def _ensure_loaded(registry, requests):
    reg = registry if isinstance(registry, Registry) else load_registry(registry)
    if isinstance(requests, tuple) and all(isinstance(r, Request) for r in requests):
        reqs = requests
    else:
        reqs = load_requests(requests, reg)
    return reg, reqs


def solve(registry, requests, budget: int = 10000) -> dict:
    """Solve root requests against an offline registry.

    Returns one of:
      {"status": "sat",     "roots": [...], "lock": {...}, "nodes_used": n}
      {"status": "unsat",   "roots": [...], "conflicts": [...], "nodes_used": n}
      {"status": "unknown", "roots": [...], "reason": ..., "nodes_used": n}
    """
    reg, reqs = _ensure_loaded(registry, requests)
    solver = _Solver(reg, reqs, budget)
    result = solver._search()
    roots = [r.id for r in reqs]
    if result is _UNKNOWN:
        return {
            "status": "unknown",
            "roots": roots,
            "reason": f"node budget exhausted after {solver.nodes_used} attempts",
            "nodes_used": solver.nodes_used,
        }
    if result is None:
        return {
            "status": "unsat",
            "roots": roots,
            "conflicts": solver.conflicts,
            "nodes_used": solver.nodes_used,
        }
    return {
        "status": "sat",
        "roots": roots,
        "lock": result,
        "nodes_used": solver.nodes_used,
    }
