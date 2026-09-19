"""Backtracking dependency/feature solver.

Deterministic search order:

* the pending package with the lexicographically smallest name is decided next;
* its candidate versions are tried newest-first (descending triple order);
* conflicts undo every effect accumulated on the branch (constraints, feature
  requests, frontier entries and the selection itself) before the next try.

Feature enabling is propagated to a fixed point after every selection and may
add dependencies and feature requests, including cycles. One node is charged
per package-version attempt; the budget is shared by the whole request and on
exhaustion the result is ``unknown`` rather than ``unsat``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from .intervals import (
    ANY,
    Constraint,
    Version,
    contains,
    intersect,
    to_json,
)
from .model import FeatureDef, PackageVersion, Registry, RootRequest

Status = Literal["sat", "unsat", "unknown"]
DEFAULT_NODE_BUDGET = 10_000


def version_key(v: Version) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


@dataclass
class SolveResult:
    """Outcome of one root request."""

    status: Status
    root_id: str
    nodes_used: int
    budget: int
    versions: dict[str, list[int]] = field(default_factory=dict)
    features: dict[str, list[str]] = field(default_factory=dict)
    dependency_sources: dict[str, list[str]] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "root_id": self.root_id,
            "nodes_used": self.nodes_used,
            "budget": self.budget,
        }
        if self.status == "sat":
            out["versions"] = self.versions
            out["features"] = self.features
            out["dependency_sources"] = self.dependency_sources
        if self.conflicts:
            out["conflicts"] = self.conflicts
        return out


class _BudgetExhausted(Exception):
    """Internal: node budget ran out; unwind immediately to ``unknown``."""


class _Conflict(Exception):
    """Internal: the current branch is infeasible."""

    def __init__(self, info: dict[str, Any]):
        self.info = info
        super().__init__(info.get("type", "conflict"))


class _Solver:
    def __init__(self, registry: Registry, request: RootRequest, budget: int):
        self.registry = registry
        self.request = request
        self.budget = budget
        self.nodes = 0

        self.cand: dict[str, PackageVersion] = {}
        self.cons: dict[str, Constraint] = {}
        self.freq: dict[str, set[str]] = {}
        self.sources: dict[str, list[str]] = {}
        self.frontier: set[str] = set()
        # Undo log; each mutation pushes a record and branch failures pop them.
        self.records: list[tuple[Any, ...]] = []
        self.conflicts: list[dict[str, Any]] = []
        self.conflict_keys: set[tuple[Any, ...]] = set()

    # ------------------------------------------------------------------ log
    def _rollback(self, mark: int) -> None:
        while len(self.records) > mark:
            rec = self.records.pop()
            kind = rec[0]
            if kind == "cons":
                _, pkg, existed, old = rec
                if existed:
                    self.cons[pkg] = old
                else:
                    self.cons.pop(pkg, None)
            elif kind == "src":
                _, pkg, added = rec
                bucket = self.sources.get(pkg)
                if bucket is not None:
                    for item in added:
                        try:
                            bucket.remove(item)
                        except ValueError:
                            pass
                    if not bucket:
                        self.sources.pop(pkg, None)
            elif kind == "freqkey":
                self.freq.pop(rec[1], None)
            elif kind == "freq":
                _, pkg, added = rec
                bucket = self.freq.get(pkg)
                if bucket is not None:
                    bucket.difference_update(added)
            elif kind == "frontier":
                self.frontier.discard(rec[1])
            elif kind == "readd_frontier":
                self.frontier.add(rec[1])
            elif kind == "cand":
                _, pkg, existed, old = rec
                if existed:
                    self.cand[pkg] = old
                else:
                    self.cand.pop(pkg, None)

    def _note(self, conflict: dict[str, Any]) -> None:
        key = repr(conflict)
        if key not in self.conflict_keys:
            self.conflict_keys.add(key)
            self.conflicts.append(conflict)

    # ------------------------------------------------------------- mutations
    def _add_constraint(self, pkg: str, c: Constraint, source: str) -> Constraint:
        existed = pkg in self.cons
        old = self.cons[pkg] if existed else list(ANY)
        new = intersect(old, c)
        self.records.append(("cons", pkg, existed, old if existed else None))
        self.cons[pkg] = new
        bucket = self.sources.setdefault(pkg, [])
        if source not in bucket:
            bucket.append(source)
            self.records.append(("src", pkg, (source,)))
        return new

    def _require_package(self, pkg: str, c: Constraint, source: str) -> None:
        """Intersect a constraint on ``pkg``; mark it pending and check feasibility."""
        new = self._add_constraint(pkg, c, source)
        if pkg not in self.cand and pkg not in self.frontier:
            self.frontier.add(pkg)
            self.records.append(("frontier", pkg))
        if not new:
            raise _Conflict(
                {
                    "type": "empty_constraint",
                    "package": pkg,
                    "sources": list(self.sources.get(pkg, ())),
                }
            )
        if pkg in self.cand and not contains(new, self.cand[pkg].version):
            raise _Conflict(
                {
                    "type": "version_conflict",
                    "package": pkg,
                    "selected_version": list(self.cand[pkg].version),
                    "added_constraint": to_json(c),
                    "required_by": source,
                }
            )

    def _add_freq(
        self, pkg: str, names: tuple[str, ...], source: str
    ) -> set[str]:
        """Enable features on ``pkg``; a request also pulls the package in."""
        if pkg not in self.freq:
            self.freq[pkg] = set()
            self.records.append(("freqkey", pkg))
        # Requesting a feature on a package requires that package to exist.
        if pkg not in self.cons and pkg not in self.cand and pkg not in self.frontier:
            self._require_package(pkg, list(ANY), source)
        bucket = self.freq[pkg]
        added = {n for n in names if n not in bucket}
        if added:
            bucket.update(added)
            self.records.append(("freq", pkg, tuple(sorted(added))))
        if pkg in self.cand:
            missing = added - set(self.cand[pkg].features)
            if missing:
                raise _Conflict(
                    {
                        "type": "missing_feature",
                        "package": pkg,
                        "version": list(self.cand[pkg].version),
                        "feature": sorted(missing)[0],
                        "requested_by": source,
                    }
                )
        return added

    # ---------------------------------------------------------- propagation
    def _propagate(self, initial: list[tuple[str, str]]) -> None:
        """Apply feature effects to a fixed point.

        ``initial`` lists (package, feature) pairs that became relevant: all
        enabled features of a freshly selected package, or features enabled on
        an already selected package. Only newly enabled features are queued, so
        the worklist is idempotent and feature cycles terminate.
        """
        queue: deque[tuple[str, str]] = deque(initial)
        queued: set[tuple[str, str]] = set(initial)
        while queue:
            pkg, fname = queue.popleft()
            queued.discard((pkg, fname))
            pv = self.cand.get(pkg)
            if pv is None or fname not in self.freq.get(pkg, set()):
                continue
            if fname not in pv.features:
                raise _Conflict(
                    {
                        "type": "missing_feature",
                        "package": pkg,
                        "version": list(pv.version),
                        "feature": fname,
                    }
                )
            fdef: FeatureDef = pv.features[fname]
            fsource = f"{pkg}=={version_key(pv.version)} feature {fname}"
            for dep, c in fdef.dependencies.items():
                self._require_package(dep, c, fsource)
            for target, fs in fdef.feature_requests.items():
                added = self._add_freq(target, fs, fsource)
                if target in self.cand:
                    for new_f in sorted(added):
                        if (target, new_f) not in queued:
                            queue.append((target, new_f))
                            queued.add((target, new_f))

    # ---------------------------------------------------------------- search
    def _charge_node(self) -> None:
        # A node is one package-version attempt; the budget bounds the count.
        if self.nodes >= self.budget:
            raise _BudgetExhausted
        self.nodes += 1

    def _solve(self) -> SolveResult | None:
        if not self.frontier:
            return self._build_sat()

        pkg = min(self.frontier)
        constraint = self.cons.get(pkg, list(ANY))
        rejected: list[dict[str, Any]] = []
        for pv in self.registry.get(pkg).versions:
            if not contains(constraint, pv.version):
                continue
            self._charge_node()
            mark = len(self.records)
            try:
                self._select(pkg, pv)
            except _Conflict as exc:
                reason = exc.info
            else:
                reason = None
                result = self._solve()
                if result is not None:
                    return result
                reason = {"type": "downstream_dead_end"}
            self._rollback(mark)
            rejected.append(
                {"version": list(pv.version), "reason": reason.get("type", "conflict")}
            )
            if reason["type"] == "downstream_dead_end":
                self._note(
                    {
                        "type": "version_dead_end",
                        "package": pkg,
                        "version": list(pv.version),
                    }
                )
            else:
                self._note(reason)
        self._note(
            {
                "type": "no_candidate",
                "package": pkg,
                "rejected": rejected,
                "sources": list(self.sources.get(pkg, ())),
                "constraint": to_json(constraint),
            }
        )
        return None

    def _select(self, pkg: str, pv: PackageVersion) -> None:
        self.records.append(("cand", pkg, pkg in self.cand, self.cand.get(pkg)))
        self.cand[pkg] = pv
        if pkg in self.frontier:
            self.frontier.discard(pkg)
            self.records.append(("readd_frontier", pkg))

        source = f"{pkg}=={version_key(pv.version)}"
        requested = self.freq.get(pkg, set())
        unknown = requested - set(pv.features)
        if unknown:
            raise _Conflict(
                {
                    "type": "missing_feature",
                    "package": pkg,
                    "version": list(pv.version),
                    "feature": sorted(unknown)[0],
                }
            )
        for dep, c in pv.dependencies.items():
            self._require_package(dep, c, source)
        self._propagate([(pkg, f) for f in sorted(requested)])

    def _build_sat(self) -> SolveResult:
        versions = {pkg: list(pv.version) for pkg, pv in sorted(self.cand.items())}
        features = {
            pkg: sorted(self.freq.get(pkg, set())) for pkg in sorted(self.cand)
        }
        dep_sources = {
            pkg: list(self.sources.get(pkg, [])) for pkg in sorted(self.cand)
        }
        return SolveResult(
            status="sat",
            root_id=self.request.root_id,
            nodes_used=self.nodes,
            budget=self.budget,
            versions=versions,
            features=features,
            dependency_sources=dep_sources,
        )

    def run(self) -> SolveResult:
        mark = len(self.records)
        seed_conflict: dict[str, Any] | None = None
        try:
            try:
                for pkg, c in sorted(self.request.requirements.items()):
                    self._require_package(pkg, c, self.request.root_id)
                for target, fs in sorted(self.request.feature_requests.items()):
                    self._add_freq(target, fs, self.request.root_id)
            except _Conflict as exc:
                seed_conflict = exc.info
            if seed_conflict is None:
                result = self._solve()
            else:
                result = None
                self._note(seed_conflict)
        except _BudgetExhausted:
            return SolveResult(
                status="unknown",
                root_id=self.request.root_id,
                nodes_used=self.nodes,
                budget=self.budget,
            )
        finally:
            self._rollback(mark)
        if result is not None:
            return result
        return SolveResult(
            status="unsat",
            root_id=self.request.root_id,
            nodes_used=self.nodes,
            budget=self.budget,
            conflicts=self.conflicts,
        )


def solve(
    registry: Registry,
    request: RootRequest,
    node_budget: int = DEFAULT_NODE_BUDGET,
) -> SolveResult:
    """Resolve one root request against an offline registry."""
    if not isinstance(node_budget, int) or node_budget <= 0:
        raise ValueError("node_budget must be a positive integer")
    return _Solver(registry, request, node_budget).run()
