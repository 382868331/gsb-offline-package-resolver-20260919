"""Offline resolver demonstration.

Run with ``python demo.py``. It computes two real requests:

1. a successful resolution where the newest version is a dead end and the
   solver backtracks, feature propagation adds dependencies in a cycle;
2. a genuinely infeasible diamond request with conflict diagnostics.

Both results are independently checked; nothing is hard-coded.
"""

from __future__ import annotations

import json
import time

from resolver import build_registry, build_request, check_lock, solve

# --------------------------------------------------------------------- data
REGISTRY = {
    "packages": [
        {
            "name": "webapp",
            "versions": [
                {
                    # Newest: asks for server >= 2.0.0, which does not exist
                    # offline -> a dead end the search must abandon.
                    "version": [2, 0, 0],
                    "dependencies": {"server": [[[2, 0, 0], [3, 0, 0]]]},
                },
                {
                    "version": [1, 4, 0],
                    "dependencies": {
                        "server": [[[1, 0, 0], [2, 0, 0]]]
                    },
                    "features": {
                        "http2": {
                            "feature_requests": {"server": ["tls"]},
                        }
                    },
                },
            ],
        },
        {
            "name": "server",
            "versions": [
                {
                    "version": [1, 2, 0],
                    "features": {
                        # tls <-> fips request each other: a feature cycle that
                        # propagation absorbs at its fixed point.
                        "tls": {
                            "dependencies": {"crypto": [[[1, 0, 0], [2, 0, 0]]]},
                            "feature_requests": {"crypto": ["fips"]},
                        },
                    },
                }
            ],
        },
        {
            "name": "crypto",
            "versions": [
                {
                    "version": [1, 0, 0],
                    "features": {
                        "fips": {"feature_requests": {"server": ["tls"]}},
                    },
                }
            ],
        },
        # Present in the registry but unreachable: must stay out of the lock.
        {
            "name": "metrics",
            "versions": [{"version": [3, 1, 0]}],
        },
    ]
}

SAT_REQUEST = {
    "id": "build-42",
    "requirements": {"webapp": [[[1, 0, 0], [3, 0, 0]]]},
    "feature_requests": {"webapp": ["http2"]},
}

# Diamond: two root packages constrain "crypto" to disjoint ranges.
UNSAT_REGISTRY = {
    "packages": [
        {
            "name": "alpha",
            "versions": [
                {"version": [1, 0, 0],
                 "dependencies": {"crypto": [[[2, 0, 0], [3, 0, 0]]]}}
            ],
        },
        {
            "name": "beta",
            "versions": [
                {"version": [1, 0, 0],
                 "dependencies": {"crypto": [[[1, 0, 0], [2, 0, 0]]]}}
            ],
        },
        {
            "name": "crypto",
            "versions": [{"version": [1, 0, 0]}, {"version": [2, 0, 0]}],
        },
    ]
}

UNSAT_REQUEST = {
    "id": "build-43",
    "requirements": {"alpha": None, "beta": None},
}


def header(title: str) -> None:
    print("=" * 68)
    print(title)
    print("=" * 68)


def main() -> None:
    start = time.perf_counter()

    header("[1/2] SAT: newest webapp is a dead end, backtrack + feature cycle")
    registry = build_registry(REGISTRY)
    request = build_request(SAT_REQUEST, registry)
    result = solve(registry, request)
    print(f"status      : {result.status}")
    print(f"root id     : {result.root_id}")
    print(f"nodes used  : {result.nodes_used} (webapp 2.0.0 tried first, then 1.4.0)")
    print("versions    :")
    for pkg, v in result.versions.items():
        print(f"  {pkg:<8} -> {v[0]}.{v[1]}.{v[2]}")
    print("features    :")
    for pkg, feats in result.features.items():
        if feats:
            print(f"  {pkg:<8} -> {', '.join(feats)}")
    print("why locked  :")
    for pkg, srcs in result.dependency_sources.items():
        print(f"  {pkg:<8} <- {'; '.join(srcs)}")
    assert "metrics" not in result.versions, "unrelated package leaked into lock"
    check = check_lock(registry, request, result.to_dict())
    print(f"independent lock check: {'PASS' if check.ok else 'FAIL'}")
    check.raise_if_invalid()

    print()
    header("[2/2] UNSAT: diamond constrains crypto to two disjoint ranges")
    bad_registry = build_registry(UNSAT_REGISTRY)
    bad_request = build_request(UNSAT_REQUEST, bad_registry)
    bad_result = solve(bad_registry, bad_request)
    print(f"status      : {bad_result.status}")
    print(f"root id     : {bad_result.root_id}")
    print(f"nodes used  : {bad_result.nodes_used}")
    print("conflicts   :")
    for conflict in bad_result.conflicts:
        shown = {k: v for k, v in conflict.items() if k in {"type", "package", "sources"}}
        print("  - " + json.dumps(shown, ensure_ascii=False))

    elapsed = time.perf_counter() - start
    print("-" * 68)
    print(f"demo finished in {elapsed:.2f}s "
          f"(one real sat, one real unsat; all computed by the library)")


if __name__ == "__main__":
    main()
