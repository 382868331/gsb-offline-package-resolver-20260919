"""Tests for registry/request validation."""

import unittest

from resolver import (
    build_registry,
    build_request,
    DuplicatePackageError,
    DuplicateRootIdError,
    DuplicateVersionError,
    ensure_unique_root_ids,
    InvalidInputError,
    UnknownPackageError,
)

GOOD = {
    "packages": [
        {
            "name": "app",
            "versions": [
                {
                    "version": [1, 0, 0],
                    "dependencies": {"lib": [[[1, 0, 0], [2, 0, 0]]]},
                }
            ],
        },
        {
            "name": "lib",
            "versions": [
                {"version": [1, 0, 0]},
                {"version": [1, 5, 0]},
            ],
        },
    ]
}


class ValidationTests(unittest.TestCase):
    def test_valid_registry_builds_newest_first(self):
        reg = build_registry(GOOD)
        self.assertEqual(
            [pv.version for pv in reg.get("lib").versions],
            [(1, 5, 0), (1, 0, 0)],
        )

    def test_duplicate_package_rejected(self):
        data = {"packages": [
            {"name": "x", "versions": [{"version": [0, 1, 0]}]},
            {"name": "x", "versions": [{"version": [0, 2, 0]}]},
        ]}
        with self.assertRaises(DuplicatePackageError):
            build_registry(data)

    def test_duplicate_version_rejected(self):
        data = {"packages": [
            {"name": "x", "versions": [
                {"version": [1, 0, 0]},
                {"version": [1, 0, 0]},
            ]},
        ]}
        with self.assertRaises(DuplicateVersionError):
            build_registry(data)

    def test_too_many_packages(self):
        data = {"packages": [
            {"name": f"p{i}", "versions": [{"version": [0, 0, 1]}]}
            for i in range(13)
        ]}
        with self.assertRaises(InvalidInputError):
            build_registry(data)

    def test_too_many_versions(self):
        data = {"packages": [
            {"name": "p", "versions": [
                {"version": [i, 0, 0]} for i in range(7)
            ]},
        ]}
        with self.assertRaises(InvalidInputError):
            build_registry(data)

    def test_unknown_dependency_reference(self):
        data = {"packages": [
            {"name": "p", "versions": [
                {"version": [1, 0, 0],
                 "dependencies": {"ghost": [[[1, 0, 0], [2, 0, 0]]]}},
            ]},
        ]}
        with self.assertRaises(UnknownPackageError):
            build_registry(data)

    def test_unknown_feature_request_target(self):
        data = {"packages": [
            {"name": "p", "versions": [
                {"version": [1, 0, 0],
                 "features": {"f": {"feature_requests": {"ghost": ["g"]}}}},
            ]},
        ]}
        with self.assertRaises(UnknownPackageError):
            build_registry(data)

    def test_root_unknown_package(self):
        reg = build_registry(GOOD)
        with self.assertRaises(UnknownPackageError):
            build_request(
                {"id": "r1", "requirements": {"ghost": [[[1, 0, 0], [2, 0, 0]]]}},
                reg,
            )

    def test_duplicate_root_ids(self):
        reg = build_registry(GOOD)
        r1 = build_request({"id": "same", "requirements": {"lib": None}}, reg)
        r2 = build_request({"id": "same", "requirements": {"app": None}}, reg)
        with self.assertRaises(DuplicateRootIdError):
            ensure_unique_root_ids([r1, r2])

    def test_bad_version_triple(self):
        with self.assertRaises(InvalidInputError):
            build_registry({"packages": [
                {"name": "p", "versions": [{"version": [1, 0]}]},
            ]})
        with self.assertRaises(ValueError):
            build_registry({"packages": [
                {"name": "p", "versions": [{"version": [1, -1, 0]}]},
            ]})


if __name__ == "__main__":
    unittest.main()
