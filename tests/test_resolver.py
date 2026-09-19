"""Tests for the offline resolver: search semantics, feature propagation,
backtracking, budget handling, validation, lock checking and exhaustive
cross-validation against the brute-force enumerator.
"""

import random
import unittest

from resolver import (
    RegistryError,
    brute_force_locks,
    check_lock,
    load_registry,
    load_requests,
    solve,
)


def pkg(name, versions):
    return {"name": name, "versions": versions}


def ver(version, deps=None, features=None):
    out = {"version": list(version)}
    if deps:
        out["deps"] = deps
    if features:
        out["features"] = features
    return out


def dep(package, ranges=None, features=None):
    out = {"package": package}
    if ranges is not None:
        out["ranges"] = ranges
    if features:
        out["features"] = features
    return out


def req(rid, package, ranges=None, features=None):
    out = {"id": rid, "package": package}
    if ranges is not None:
        out["ranges"] = ranges
    if features:
        out["features"] = features
    return out


def locked_versions(result):
    return {
        name: tuple(entry["version"])
        for name, entry in result["lock"]["packages"].items()
    }


class SearchSemanticsTests(unittest.TestCase):
    def test_backtrack_from_highest_version_dead_end(self):
        # a 2.0.0 is tried first (descending) but its range on c is a dead
        # end; the solver must backtrack to a 1.0.0.
        registry = [
            pkg("a", [
                ver((2, 0, 0), deps=[dep("c", [[[2, 0, 0], [3, 0, 0]]])]),
                ver((1, 0, 0), deps=[dep("c", [[[1, 0, 0], [2, 0, 0]]])]),
            ]),
            pkg("c", [ver((1, 0, 0))]),
        ]
        result = solve(registry, [req("r1", "a")])
        self.assertEqual(result["status"], "sat")
        self.assertEqual(
            locked_versions(result), {"a": (1, 0, 0), "c": (1, 0, 0)}
        )
        # one node per attempt: a=2.0.0 (fails), a=1.0.0, c=1.0.0
        self.assertEqual(result["nodes_used"], 3)
        self.assertEqual(check_lock(registry, [req("r1", "a")], result["lock"]), [])

    def test_diamond_conflict_unsat(self):
        registry = [
            pkg("left", [ver((1, 0, 0), deps=[dep("shared", [[[1, 0, 0], [2, 0, 0]]])])]),
            pkg("right", [ver((1, 0, 0), deps=[dep("shared", [[[2, 0, 0], [3, 0, 0]]])])]),
            pkg("shared", [ver((1, 0, 0)), ver((2, 0, 0))]),
        ]
        requests = [req("r-left", "left"), req("r-right", "right")]
        result = solve(registry, requests)
        self.assertEqual(result["status"], "unsat")
        self.assertEqual(result["roots"], ["r-left", "r-right"])
        self.assertTrue(result["conflicts"])
        self.assertTrue(
            any(c["package"] == "shared" for c in result["conflicts"]),
            "diagnostics should mention the conflicting package",
        )

    def test_cyclic_features_reach_fixpoint(self):
        # a[x] -> b{y}; b[y] -> a{z}: a feature cycle across packages.
        registry = [
            pkg("a", [ver((1, 0, 0), features={
                "x": [dep("b", features=["y"])],
                "z": [],
            })]),
            pkg("b", [ver((1, 0, 0), features={
                "y": [dep("a", features=["z"])],
            })]),
        ]
        requests = [req("r1", "a", features=["x"])]
        result = solve(registry, requests)
        self.assertEqual(result["status"], "sat")
        packages = result["lock"]["packages"]
        self.assertEqual(packages["a"]["features"], ["x", "z"])
        self.assertEqual(packages["b"]["features"], ["y"])
        self.assertEqual(check_lock(registry, requests, result["lock"]), [])

    def test_self_cycle_feature(self):
        registry = [
            pkg("a", [ver((1, 0, 0), features={
                "x": [dep("a", features=["y"])],
                "y": [],
            })]),
        ]
        result = solve(registry, [req("r1", "a", features=["x"])])
        self.assertEqual(result["status"], "sat")
        self.assertEqual(result["lock"]["packages"]["a"]["features"], ["x", "y"])

    def test_backtrack_undoes_feature_and_dependency_effects(self):
        # a 2.0.0 (tried first) requests feature x on c and an impossible
        # range on d. After backtracking to a 1.0.0, c must not keep
        # feature x and d must not appear at all.
        registry = [
            pkg("a", [
                ver((2, 0, 0), deps=[
                    dep("c", features=["x"]),
                    dep("d", [[[9, 0, 0], [10, 0, 0]]]),
                ]),
                ver((1, 0, 0), deps=[dep("c")]),
            ]),
            pkg("c", [ver((1, 0, 0), features={"x": []})]),
            pkg("d", [ver((1, 0, 0))]),
        ]
        requests = [req("r1", "a")]
        result = solve(registry, requests)
        self.assertEqual(result["status"], "sat")
        packages = result["lock"]["packages"]
        self.assertEqual(tuple(packages["a"]["version"]), (1, 0, 0))
        self.assertEqual(packages["c"]["features"], [])
        self.assertNotIn("d", packages)
        self.assertEqual(check_lock(registry, requests, result["lock"]), [])

    def test_unknown_feature_rejects_candidate(self):
        # 2.0.0 does not define the requested feature, so only 1.0.0 is a
        # viable candidate even though it is older.
        registry = [
            pkg("a", [
                ver((2, 0, 0)),
                ver((1, 0, 0), features={"x": []}),
            ]),
        ]
        requests = [req("r1", "a", features=["x"])]
        result = solve(registry, requests)
        self.assertEqual(result["status"], "sat")
        self.assertEqual(locked_versions(result), {"a": (1, 0, 0)})
        self.assertEqual(check_lock(registry, requests, result["lock"]), [])

    def test_unknown_feature_on_all_versions_is_unsat(self):
        registry = [pkg("a", [ver((1, 0, 0)), ver((2, 0, 0))])]
        result = solve(registry, [req("r1", "a", features=["nope"])])
        self.assertEqual(result["status"], "unsat")

    def test_unrelated_package_not_in_lock(self):
        registry = [
            pkg("a", [ver((1, 0, 0), deps=[dep("b")])]),
            pkg("b", [ver((1, 0, 0))]),
            pkg("zzz", [ver((9, 9, 9))]),
        ]
        requests = [req("r1", "a")]
        result = solve(registry, requests)
        self.assertEqual(result["status"], "sat")
        self.assertEqual(set(result["lock"]["packages"]), {"a", "b"})
        self.assertEqual(check_lock(registry, requests, result["lock"]), [])

    def test_budget_exhaustion_returns_unknown_not_unsat(self):
        registry = [
            pkg("a", [ver((1, 0, 0), deps=[dep("b")])]),
            pkg("b", [ver((1, 0, 0))]),
        ]
        requests = [req("r1", "a")]
        unknown = solve(registry, requests, budget=1)
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(unknown["roots"], ["r1"])
        # the same instance is sat with enough budget: unknown was not a
        # disguised unsat proof
        solved = solve(registry, requests, budget=2)
        self.assertEqual(solved["status"], "sat")

    def test_zero_budget_is_unknown(self):
        registry = [pkg("a", [ver((1, 0, 0))])]
        result = solve(registry, [req("r1", "a")], budget=0)
        self.assertEqual(result["status"], "unknown")

    def test_highest_feasible_version_preferred(self):
        registry = [pkg("a", [ver((1, 0, 0)), ver((2, 0, 0)), ver((3, 0, 0))])]
        result = solve(registry, [req("r1", "a")])
        self.assertEqual(result["status"], "sat")
        self.assertEqual(locked_versions(result), {"a": (3, 0, 0)})
        self.assertEqual(result["nodes_used"], 1)

    def test_multi_source_constraints_intersect(self):
        registry = [
            pkg("a", [ver((1, 0, 0), deps=[dep("c", [[[1, 0, 0], [3, 0, 0]]])])]),
            pkg("b", [ver((1, 0, 0), deps=[dep("c", [[[2, 0, 0], [4, 0, 0]]])])]),
            pkg("c", [ver((1, 5, 0)), ver((2, 5, 0)), ver((3, 5, 0))]),
        ]
        requests = [req("r1", "a"), req("r2", "b")]
        result = solve(registry, requests)
        self.assertEqual(result["status"], "sat")
        # intersection of [1,3) and [2,4) is [2,3): only c 2.5.0 works
        self.assertEqual(locked_versions(result)["c"], (2, 5, 0))
        self.assertEqual(check_lock(registry, requests, result["lock"]), [])


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.registry = [
            pkg("a", [ver((1, 0, 0), deps=[dep("b", features=["x"])])]),
            pkg("b", [ver((1, 0, 0), features={"x": [dep("c")]})]),
            pkg("c", [ver((1, 0, 0))]),
        ]
        self.requests = [req("r1", "a")]
        result = solve(self.registry, self.requests)
        self.assertEqual(result["status"], "sat")
        self.lock = result["lock"]

    def test_checker_accepts_solver_lock(self):
        self.assertEqual(check_lock(self.registry, self.requests, self.lock), [])

    def test_checker_rejects_unrelated_package(self):
        # c2 exists in the registry but is not reachable from the roots
        registry = self.registry + [pkg("c2", [ver((1, 0, 0))])]
        bad = {
            "roots": ["r1"],
            "packages": {
                **self.lock["packages"],
                "c2": {"version": [1, 0, 0], "features": [], "dependencies": []},
            },
        }
        errors = check_lock(registry, self.requests, bad)
        self.assertTrue(any("not reachable" in e for e in errors), errors)

    def test_checker_rejects_missing_package(self):
        bad = {
            "roots": ["r1"],
            "packages": {k: v for k, v in self.lock["packages"].items() if k != "c"},
        }
        errors = check_lock(self.registry, self.requests, bad)
        self.assertTrue(any("missing" in e for e in errors), errors)

    def test_checker_rejects_wrong_version(self):
        registry = [
            pkg("a", [ver((1, 0, 0), deps=[dep("b", [[[2, 0, 0], [3, 0, 0]]])])]),
            pkg("b", [ver((1, 0, 0)), ver((2, 0, 0))]),
        ]
        requests = [req("r1", "a")]
        bad = {
            "roots": ["r1"],
            "packages": {
                "a": {"version": [1, 0, 0], "features": [], "dependencies": [
                    {"package": "b", "ranges": [[[2, 0, 0], [3, 0, 0]]],
                     "features": [], "source": "a 1.0.0 (required)"}]},
                "b": {"version": [1, 0, 0], "features": [], "dependencies": []},
            },
        }
        errors = check_lock(registry, requests, bad)
        self.assertTrue(any("violates" in e for e in errors), errors)

    def test_checker_rejects_wrong_features(self):
        bad = {
            "roots": ["r1"],
            "packages": {
                **self.lock["packages"],
                "b": {**self.lock["packages"]["b"], "features": []},
            },
        }
        errors = check_lock(self.registry, self.requests, bad)
        self.assertTrue(any("features" in e for e in errors), errors)

    def test_checker_rejects_wrong_roots(self):
        bad = {**self.lock, "roots": ["somebody-else"]}
        errors = check_lock(self.registry, self.requests, bad)
        self.assertTrue(any("roots" in e for e in errors), errors)


class ValidationTests(unittest.TestCase):
    def test_duplicate_package_names_rejected(self):
        with self.assertRaises(RegistryError):
            load_registry([pkg("a", [ver((1, 0, 0))]), pkg("a", [ver((2, 0, 0))])])

    def test_duplicate_versions_rejected(self):
        with self.assertRaises(RegistryError):
            load_registry([pkg("a", [ver((1, 0, 0)), ver((1, 0, 0))])])

    def test_duplicate_root_ids_rejected(self):
        registry = load_registry([pkg("a", [ver((1, 0, 0))])])
        with self.assertRaises(RegistryError):
            load_requests([req("r1", "a"), req("r1", "a")], registry)

    def test_unknown_package_reference_rejected(self):
        with self.assertRaises(RegistryError):
            load_registry([pkg("a", [ver((1, 0, 0), deps=[dep("ghost")])])])
        registry = load_registry([pkg("a", [ver((1, 0, 0))])])
        with self.assertRaises(RegistryError):
            load_requests([req("r1", "ghost")], registry)

    def test_bad_version_shape_rejected(self):
        with self.assertRaises(RegistryError):
            load_registry([pkg("a", [ver((1, 0))])])
        with self.assertRaises(RegistryError):
            load_registry([pkg("a", [ver((1, 0, -1))])])

    def test_size_limits_enforced(self):
        many = [pkg(f"p{i}", [ver((1, 0, 0))]) for i in range(13)]
        with self.assertRaises(RegistryError):
            load_registry(many)
        many_versions = [pkg("a", [ver((i, 0, 0)) for i in range(7)])]
        with self.assertRaises(RegistryError):
            load_registry(many_versions)


def _random_dep(rng, names):
    out = {"package": rng.choice(names)}
    if rng.random() < 0.7:
        lo_major = rng.randint(1, 3)
        out["ranges"] = [[[lo_major, 0, 0], [lo_major + rng.randint(1, 2), 0, 0]]]
    if rng.random() < 0.35:
        out["features"] = [f"f{rng.randint(0, 1)}"]
    return out


def _random_instance(rng):
    n = rng.randint(2, 4)
    names = [f"p{i}" for i in range(n)]
    registry = []
    for name in names:
        versions = []
        for vi in range(rng.randint(1, 3)):
            entry = {"version": [vi + 1, rng.randint(0, 1), 0]}
            deps = [_random_dep(rng, names) for _ in range(rng.randint(0, 2))]
            if deps:
                entry["deps"] = deps
            features = {}
            for fi in range(rng.randint(0, 2)):
                fdeps = [_random_dep(rng, names) for _ in range(rng.randint(0, 1))]
                features[f"f{fi}"] = fdeps
            if features:
                entry["features"] = features
            versions.append(entry)
        registry.append({"name": name, "versions": versions})
    root_pkg = rng.choice(names)
    request = {"id": "root", "package": root_pkg}
    if rng.random() < 0.5:
        request["features"] = [f"f{rng.randint(0, 1)}"]
    return registry, [request]


class CrossValidationTests(unittest.TestCase):
    def test_solver_matches_brute_force_on_random_small_instances(self):
        rng = random.Random(20260920)
        counts = {"sat": 0, "unsat": 0}
        for _ in range(60):
            registry, requests = _random_instance(rng)
            expected = brute_force_locks(registry, requests)
            result = solve(registry, requests, budget=100000)
            if expected:
                counts["sat"] += 1
                self.assertEqual(
                    result["status"], "sat",
                    f"brute force found locks but solver said {result['status']}",
                )
                got = frozenset(
                    (name, tuple(entry["version"]))
                    for name, entry in result["lock"]["packages"].items()
                )
                self.assertIn(got, expected)
                self.assertEqual(
                    check_lock(registry, requests, result["lock"]), [],
                    "checker must accept every solver lock",
                )
            else:
                counts["unsat"] += 1
                self.assertEqual(
                    result["status"], "unsat",
                    f"brute force found no lock but solver said {result['status']}",
                )
                self.assertTrue(result["conflicts"])
        # the sample must actually exercise both outcomes
        self.assertGreater(counts["sat"], 0)
        self.assertGreater(counts["unsat"], 0)


if __name__ == "__main__":
    unittest.main()
