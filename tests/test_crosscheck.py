"""Exhaustive cross-validation against an independent brute-force oracle.

Small registries (<=4 packages, <=3 versions each) are generated with a fixed
seed; every package is assigned either a concrete version or "absent" by
enumeration, and a plain reference implementation decides feasibility. The
solver's sat/unsat verdict must match the oracle, and every sat lock must pass
the independent checker.
"""

import itertools
import random
import unittest

from resolver import build_registry, build_request, check_lock, solve
from resolver.intervals import contains

NAMES = ["p0", "p1", "p2", "p3"]


# --------------------------------------------------------------------- oracle
def _feasible(registry, request, assign):
    present = {p for p, v in assign.items() if v is not None}
    selected = {
        p: registry.get(p).version_map()[v] for p, v in assign.items() if v is not None
    }

    for pkg, constraint in request.requirements.items():
        if pkg not in present or not contains(constraint, assign[pkg]):
            return False
    for target in request.feature_requests:
        if target not in present:
            return False

    # Feature least fixed point; every enabled feature must exist.
    enabled: dict[str, set[str]] = {
        t: set(fs) for t, fs in request.feature_requests.items()
    }
    changed = True
    while changed:
        changed = False
        for pkg in list(enabled):
            pv = selected[pkg]
            for fname in list(enabled[pkg]):
                if fname not in pv.features:
                    return False
                fdef = pv.features[fname]
                for dep, constraint in fdef.dependencies.items():
                    if dep not in present or not contains(constraint, assign[dep]):
                        return False
                for target, feats in fdef.feature_requests.items():
                    if target not in present:
                        return False
                    bucket = enabled.setdefault(target, set())
                    for f in feats:
                        if f not in bucket:
                            bucket.add(f)
                            changed = True

    # Mandatory dependencies of every present version.
    for pkg in present:
        for dep, constraint in selected[pkg].dependencies.items():
            if dep not in present or not contains(constraint, assign[dep]):
                return False

    # Reachability closure: the present set must equal it exactly.
    reachable = set(request.requirements) | set(request.feature_requests)
    grown = True
    while grown:
        grown = False
        for pkg in list(reachable):
            if pkg not in present:
                return False
            pv = selected[pkg]
            for dep in pv.dependencies:
                if dep not in reachable:
                    reachable.add(dep)
                    grown = True
            for fname in enabled.get(pkg, ()):
                fdef = pv.features[fname]
                for dep in fdef.dependencies:
                    if dep not in reachable:
                        reachable.add(dep)
                        grown = True
                for target in fdef.feature_requests:
                    if target not in reachable:
                        reachable.add(target)
                        grown = True
    return reachable == present


def brute_force_sat(registry, request) -> bool:
    choice_lists = [
        [None] + [pv.version for pv in registry.get(name).versions]
        for name in registry.packages
    ]
    for combo in itertools.product(*choice_lists):
        assign = dict(zip(registry.packages, combo))
        if _feasible(registry, request, assign):
            return True
    return False


# --------------------------------------------------------------- random cases
def random_interval(rng):
    if rng.random() < 0.3:
        return None
    a, b = sorted((rng.randint(0, 3), rng.randint(0, 3)))
    if a == b:
        b += 1
    return [[[a, 0, 0], [b, 0, 0]]]


def random_case(rng):
    n = rng.randint(1, 4)
    names = NAMES[:n]
    packages = []
    for name in names:
        count = rng.randint(1, 3)
        triples = set()
        versions = []
        while len(triples) < count:
            triple = (rng.randint(0, 3), rng.randint(0, 2), rng.randint(0, 2))
            triples.add(triple)
        for triple in triples:
            entry: dict = {"version": list(triple)}
            deps = {}
            if rng.random() < 0.6:
                deps[rng.choice(names)] = random_interval(rng)
            if deps:
                entry["dependencies"] = deps
            features = {}
            if rng.random() < 0.5:
                fname = f"f{rng.randint(0, 1)}"
                fdef: dict = {}
                if rng.random() < 0.5:
                    fdef["dependencies"] = {rng.choice(names): random_interval(rng)}
                if rng.random() < 0.5:
                    fdef["feature_requests"] = {
                        rng.choice(names): [f"f{rng.randint(0, 1)}"]
                    }
                features[fname] = fdef
            if features:
                entry["features"] = features
            versions.append(entry)
        packages.append({"name": name, "versions": versions})

    req: dict = {}
    for name in names:
        if rng.random() < 0.5:
            req[name] = random_interval(rng)
    freq = {}
    if rng.random() < 0.4:
        freq[rng.choice(names)] = [f"f{rng.randint(0, 1)}"]
    request = {"id": "cross-root", "requirements": req}
    if freq:
        request["feature_requests"] = freq
    return {"packages": packages}, request


class CrossValidationTests(unittest.TestCase):
    SAMPLES = 300

    def test_random_small_tables_match_brute_force(self):
        rng = random.Random(20260920)
        sat_count = unsat_count = 0
        for _ in range(self.SAMPLES):
            reg_data, req_data = random_case(rng)
            registry = build_registry(reg_data)
            request = build_request(req_data, registry)
            result = solve(registry, request, node_budget=10_000)
            expected = brute_force_sat(registry, request)
            self.assertEqual(
                result.status == "sat",
                expected,
                msg=f"solver/oracle mismatch on {req_data} over {reg_data}",
            )
            if expected:
                sat_count += 1
                check = check_lock(registry, request, result.to_dict())
                check.raise_if_invalid()
            else:
                unsat_count += 1
                self.assertEqual(result.status, "unsat")
                self.assertTrue(result.conflicts)
        # The fixed seed must actually exercise both outcomes.
        self.assertGreater(sat_count, 20)
        self.assertGreater(unsat_count, 20)


class CheckerRejectionTests(unittest.TestCase):
    REGISTRY = {
        "packages": [
            {"name": "a", "versions": [
                {"version": [1, 0, 0], "dependencies": {"b": None}, "features": {
                    "ssl": {"feature_requests": {"b": ["crypto"]}},
                }},
            ]},
            {"name": "b", "versions": [
                {"version": [1, 0, 0], "features": {"crypto": {}}},
            ]},
            {"name": "extra", "versions": [{"version": [1, 0, 0]}]},
        ]
    }

    def _sat_lock(self):
        reg = build_registry(self.REGISTRY)
        req = build_request(
            {"id": "chk", "requirements": {"a": None},
             "feature_requests": {"a": ["ssl"]}},
            reg,
        )
        result = solve(reg, req)
        self.assertEqual(result.status, "sat")
        return reg, req, result.to_dict()

    def test_good_lock_passes(self):
        reg, req, lock = self._sat_lock()
        self.assertTrue(check_lock(reg, req, lock).ok)

    def test_wrong_version_rejected(self):
        reg, req, lock = self._sat_lock()
        lock["versions"]["b"] = [9, 9, 9]
        self.assertFalse(check_lock(reg, req, lock).ok)

    def test_missing_dependency_rejected(self):
        reg, req, lock = self._sat_lock()
        del lock["versions"]["b"]
        lock["features"].pop("b", None)
        self.assertFalse(check_lock(reg, req, lock).ok)

    def test_unrelated_package_rejected(self):
        reg, req, lock = self._sat_lock()
        lock["versions"]["extra"] = [1, 0, 0]
        lock["dependency_sources"]["extra"] = ["nowhere"]
        self.assertFalse(check_lock(reg, req, lock).ok)

    def test_wrong_feature_fixpoint_rejected(self):
        reg, req, lock = self._sat_lock()
        lock["features"]["a"] = []
        self.assertFalse(check_lock(reg, req, lock).ok)

    def test_undeclared_feature_rejected(self):
        reg, req, lock = self._sat_lock()
        lock["features"]["b"] = ["crypto", "ghost"]
        self.assertFalse(check_lock(reg, req, lock).ok)


if __name__ == "__main__":
    unittest.main()
