"""Solver behaviour tests for the scenarios required by TASK.md."""

import unittest

from resolver import build_registry, build_request, check_lock, solve


def solve_dict(registry_data, request_data, budget=10_000):
    reg = build_registry(registry_data)
    req = build_request(request_data, reg)
    return reg, req, solve(reg, req, budget)


class HighestVersionDeadEndTests(unittest.TestCase):
    """最高版本死路时必须回退到较低版本，不能贪心定案。"""

    REGISTRY = {
        "packages": [
            {
                "name": "a",
                "versions": [
                    {"version": [2, 0, 0],
                     "dependencies": {"b": [[[2, 0, 0], [3, 0, 0]]]}},
                    {"version": [1, 0, 0],
                     "dependencies": {"b": [[[1, 0, 0], [2, 0, 0]]]}},
                ],
            },
            {"name": "b", "versions": [{"version": [1, 0, 0]}]},
        ]
    }

    def test_falls_back_from_dead_end(self):
        reg, req, result = solve_dict(
            self.REGISTRY,
            {"id": "r1", "requirements": {"a": [[[1, 0, 0], [3, 0, 0]]]}},
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(result.versions, {"a": [1, 0, 0], "b": [1, 0, 0]})
        # a 2.0.0 attempted (node 1) and rejected, a 1.0.0 (node 2), b (node 3)
        self.assertEqual(result.nodes_used, 3)
        check = check_lock(reg, req, result.to_dict())
        check.raise_if_invalid()


class DiamondConflictTests(unittest.TestCase):
    """菱形依赖：两个来源约束取交，交集为空即 unsat。"""

    REGISTRY = {
        "packages": [
            {"name": "a", "versions": [
                {"version": [1, 0, 0],
                 "dependencies": {"x": [[[2, 0, 0], [3, 0, 0]]]}},
            ]},
            {"name": "c", "versions": [
                {"version": [1, 0, 0],
                 "dependencies": {"x": [[[1, 0, 0], [2, 0, 0]]]}},
            ]},
            {"name": "x", "versions": [
                {"version": [1, 0, 0]},
                {"version": [2, 0, 0]},
            ]},
        ]
    }

    def test_diamond_intersection_empty_is_unsat(self):
        reg, req, result = solve_dict(
            self.REGISTRY,
            {"id": "diamond", "requirements": {"a": None, "c": None}},
        )
        self.assertEqual(result.status, "unsat")
        self.assertEqual(result.root_id, "diamond")
        self.assertTrue(result.conflicts)
        kinds = {c.get("type") for c in result.conflicts}
        self.assertIn("empty_constraint", kinds)

    def test_diamond_with_overlap_is_sat(self):
        registry = {
            "packages": [
                {"name": "a", "versions": [
                    {"version": [1, 0, 0],
                     "dependencies": {"x": [[[1, 0, 0], [3, 0, 0]]]}},
                ]},
                {"name": "c", "versions": [
                    {"version": [1, 0, 0],
                     "dependencies": {"x": [[[2, 0, 0], [4, 0, 0]]]}},
                ]},
                {"name": "x", "versions": [
                    {"version": [1, 0, 0]},
                    {"version": [2, 0, 0]},
                ]},
            ]
        }
        reg, req, result = solve_dict(
            registry,
            {"id": "diamond-ok", "requirements": {"a": None, "c": None}},
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(result.versions["x"], [2, 0, 0])
        check_lock(reg, req, result.to_dict()).raise_if_invalid()


class CyclicFeatureTests(unittest.TestCase):
    """特性互相请求形成环，传播到固定点且必须终止。"""

    REGISTRY = {
        "packages": [
            {"name": "a", "versions": [
                {"version": [1, 0, 0], "features": {
                    "fa": {
                        "dependencies": {"b": None},
                        "feature_requests": {"b": ["fb"]},
                    },
                }},
            ]},
            {"name": "b", "versions": [
                {"version": [1, 0, 0], "features": {
                    "fb": {"feature_requests": {"a": ["fa"]}},
                }},
            ]},
        ]
    }

    def test_feature_cycle_reaches_fixpoint(self):
        reg, req, result = solve_dict(
            self.REGISTRY,
            {"id": "cyc", "requirements": {"a": None},
             "feature_requests": {"a": ["fa"]}},
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(result.features["a"], ["fa"])
        self.assertEqual(result.features["b"], ["fb"])
        self.assertEqual(set(result.versions), {"a", "b"})
        check_lock(reg, req, result.to_dict()).raise_if_invalid()


class BacktrackingUndoTests(unittest.TestCase):
    """失败分支加入的特性/依赖/未决包必须在回退后彻底撤销。"""

    REGISTRY = {
        "packages": [
            {"name": "a", "versions": [
                {"version": [2, 0, 0], "features": {
                    "plug": {"dependencies": {"c": [[[2, 0, 0], [3, 0, 0]]]}},
                }},
                {"version": [1, 0, 0], "features": {"plug": {}}},
            ]},
            {"name": "c", "versions": [{"version": [1, 0, 0]}]},
        ]
    }

    def test_branch_effects_undone(self):
        reg, req, result = solve_dict(
            self.REGISTRY,
            {"id": "undo", "requirements": {"a": None},
             "feature_requests": {"a": ["plug"]}},
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(result.versions, {"a": [1, 0, 0]})
        self.assertNotIn("c", result.versions)
        self.assertNotIn("c", result.dependency_sources)
        check_lock(reg, req, result.to_dict()).raise_if_invalid()


class UnknownFeatureCandidateTests(unittest.TestCase):
    """候选版本没有被请求的特性即不可行；含该特性的较低版本可被选中。"""

    REGISTRY = {
        "packages": [
            {"name": "a", "versions": [
                {"version": [2, 0, 0], "features": {"other": {}}},
                {"version": [1, 0, 0], "features": {"ssl": {}}},
            ]},
        ]
    }

    def test_reject_candidate_without_feature_then_succeed(self):
        reg, req, result = solve_dict(
            self.REGISTRY,
            {"id": "uf", "requirements": {"a": None},
             "feature_requests": {"a": ["ssl"]}},
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(result.versions["a"], [1, 0, 0])
        self.assertEqual(result.features["a"], ["ssl"])
        self.assertEqual(result.nodes_used, 2)
        check_lock(reg, req, result.to_dict()).raise_if_invalid()

    def test_feature_missing_on_every_version_is_unsat(self):
        reg, req, result = solve_dict(
            self.REGISTRY,
            {"id": "uf2", "requirements": {"a": None},
             "feature_requests": {"a": ["nope"]}},
        )
        self.assertEqual(result.status, "unsat")
        self.assertEqual(result.root_id, "uf2")
        kinds = {c.get("type") for c in result.conflicts}
        self.assertIn("missing_feature", kinds)


class UnrelatedPackageTests(unittest.TestCase):
    """注册表中的无关包不得进入锁单。"""

    REGISTRY = {
        "packages": [
            {"name": "a", "versions": [
                {"version": [1, 0, 0], "dependencies": {"b": None}},
            ]},
            {"name": "b", "versions": [{"version": [1, 0, 0]}]},
            {"name": "zoo", "versions": [
                {"version": [9, 9, 9], "dependencies": {"b": None}},
            ]},
        ]
    }

    def test_lock_is_exact_closure(self):
        reg, req, result = solve_dict(
            self.REGISTRY, {"id": "rel", "requirements": {"a": None}}
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(set(result.versions), {"a", "b"})
        self.assertNotIn("zoo", result.versions)
        check_lock(reg, req, result.to_dict()).raise_if_invalid()


class NodeBudgetTests(unittest.TestCase):
    """预算耗尽返回 unknown；放宽预算后同一请求必须可解。"""

    REGISTRY = HighestVersionDeadEndTests.REGISTRY

    def test_budget_exhaustion_is_unknown_not_unsat(self):
        _, _, result = solve_dict(
            self.REGISTRY,
            {"id": "bud", "requirements": {"a": [[[1, 0, 0], [3, 0, 0]]]}},
            budget=2,
        )
        self.assertEqual(result.status, "unknown")
        self.assertGreaterEqual(result.nodes_used, 2)
        self.assertEqual(result.budget, 2)

    def test_same_request_sat_with_larger_budget(self):
        _, _, result = solve_dict(
            self.REGISTRY,
            {"id": "bud", "requirements": {"a": [[[1, 0, 0], [3, 0, 0]]]}},
            budget=100,
        )
        self.assertEqual(result.status, "sat")


class FeatureUnionAndOrderTests(unittest.TestCase):
    """同包特性取并；首个解遵循版本降序与包名升序。"""

    def test_features_union_from_multiple_requesters(self):
        registry = {
            "packages": [
                {"name": "a", "versions": [
                    {"version": [1, 0, 0], "features": {
                        "send": {
                            "dependencies": {"b": None},
                            "feature_requests": {"b": ["f2"]},
                        },
                    }},
                ]},
                {"name": "b", "versions": [
                    {"version": [1, 0, 0], "features": {"f1": {}, "f2": {}}},
                ]},
            ]
        }
        reg, req, result = solve_dict(
            registry,
            {"id": "union", "requirements": {"a": None},
             "feature_requests": {"a": ["send"], "b": ["f1"]}},
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(result.features["b"], ["f1", "f2"])
        check_lock(reg, req, result.to_dict()).raise_if_invalid()

    def test_newest_version_is_first_solution(self):
        registry = {"packages": [
            {"name": "a", "versions": [
                {"version": [1, 0, 0]},
                {"version": [2, 0, 0]},
                {"version": [3, 0, 0]},
            ]},
        ]}
        _, _, result = solve_dict(
            registry, {"id": "ord", "requirements": {"a": None}}
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(result.versions["a"], [3, 0, 0])
        self.assertEqual(result.nodes_used, 1)

    def test_empty_constraint_is_unsat(self):
        registry = {"packages": [
            {"name": "a", "versions": [
                {"version": [1, 0, 0], "dependencies": {"b": []}},
            ]},
            {"name": "b", "versions": [{"version": [1, 0, 0]}]},
        ]}
        _, _, result = solve_dict(
            registry, {"id": "ec", "requirements": {"a": None}}
        )
        self.assertEqual(result.status, "unsat")

    def test_root_empty_range_is_unsat_not_exception(self):
        registry = {"packages": [
            {"name": "a", "versions": [{"version": [1, 0, 0]}]},
        ]}
        _, _, result = solve_dict(
            registry, {"id": "empty", "requirements": {"a": []}}
        )
        self.assertEqual(result.status, "unsat")
        self.assertEqual(result.root_id, "empty")
        kinds = {c.get("type") for c in result.conflicts}
        self.assertIn("empty_constraint", kinds)

    def test_feature_request_only_pulls_package_in(self):
        registry = {"packages": [
            {"name": "a", "versions": [
                {"version": [1, 0, 0], "features": {"ui": {}}},
            ]},
        ]}
        reg, req, result = solve_dict(
            registry,
            {"id": "freq-only", "requirements": {},
             "feature_requests": {"a": ["ui"]}},
        )
        self.assertEqual(result.status, "sat")
        self.assertEqual(set(result.versions), {"a"})
        self.assertEqual(result.features["a"], ["ui"])
        check_lock(reg, req, result.to_dict()).raise_if_invalid()

    def test_budget_of_one_is_unknown_on_two_try_path(self):
        _, _, result = solve_dict(
            HighestVersionDeadEndTests.REGISTRY,
            {"id": "b1", "requirements": {"a": [[[1, 0, 0], [3, 0, 0]]]}},
            budget=1,
        )
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.nodes_used, 1)


if __name__ == "__main__":
    unittest.main()
