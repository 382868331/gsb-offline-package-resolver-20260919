"""Demo for the offline resolver.

Shows, with results actually computed by the library:
  1. a normal sat run (backtracking past a dead-end highest version,
     feature propagation, diamond dependency, unrelated package excluded)
     plus independent lock checking;
  2. a real failure: a diamond conflict that is genuinely unsat, with
     diagnostics, and the same registry reporting unknown under a tiny
     node budget.

Run: python demo.py
"""

from resolver import check_lock, solve

REGISTRY = [
    {"name": "app", "versions": [
        {"version": [1, 0, 0], "deps": [
            {"package": "web", "ranges": [[[1, 0, 0], [3, 0, 0]]], "features": ["tls"]},
            {"package": "util", "ranges": [[[1, 0, 0], [2, 0, 0]]]},
        ]},
    ]},
    {"name": "web", "versions": [
        # highest version is a dead end: json [2,3) does not exist
        {"version": [2, 0, 0],
         "deps": [{"package": "json", "ranges": [[[2, 0, 0], [3, 0, 0]]]}],
         "features": {"tls": [{"package": "net", "features": ["ssl"]}]}},
        {"version": [1, 0, 0],
         "deps": [{"package": "json", "ranges": [[[1, 0, 0], [2, 0, 0]]]}],
         "features": {"tls": [{"package": "net", "features": ["ssl"]}]}},
    ]},
    {"name": "json", "versions": [
        {"version": [1, 5, 0],
         "deps": [{"package": "util", "ranges": [[[1, 0, 0], [1, 9, 0]]]}]},
    ]},
    {"name": "net", "versions": [
        {"version": [3, 1, 0],
         "features": {"ssl": [{"package": "crypto"}]}},
    ]},
    {"name": "crypto", "versions": [{"version": [0, 9, 0]}]},
    {"name": "util", "versions": [{"version": [1, 2, 0]}]},
    {"name": "unused", "versions": [{"version": [9, 9, 9]}]},
]

REQUESTS = [{"id": "root-app", "package": "app"}]

CONFLICT_REGISTRY = [
    {"name": "left", "versions": [
        {"version": [1, 0, 0],
         "deps": [{"package": "shared", "ranges": [[[1, 0, 0], [2, 0, 0]]]}]},
    ]},
    {"name": "right", "versions": [
        {"version": [1, 0, 0],
         "deps": [{"package": "shared", "ranges": [[[2, 0, 0], [3, 0, 0]]]}]},
    ]},
    {"name": "shared", "versions": [
        {"version": [1, 0, 0]}, {"version": [2, 0, 0]},
    ]},
]

CONFLICT_REQUESTS = [
    {"id": "root-left", "package": "left"},
    {"id": "root-right", "package": "right"},
]


def print_lock(lock):
    for name, entry in lock["packages"].items():
        version = ".".join(str(p) for p in entry["version"])
        features = ",".join(entry["features"]) or "-"
        print(f"  {name} {version}  features=[{features}]")
        for d in entry["dependencies"]:
            feats = ",".join(d["features"]) or "-"
            print(f"    -> {d['package']} ranges={d['ranges']} features=[{feats}]")
            print(f"       source: {d['source']}")


def main():
    print("=" * 72)
    print("CASE 1: solvable request (dead-end highest version, features, diamond)")
    print("=" * 72)
    result = solve(REGISTRY, REQUESTS, budget=1000)
    print(f"status: {result['status']}  (nodes used: {result['nodes_used']})")
    print_lock(result["lock"])
    errors = check_lock(REGISTRY, REQUESTS, result["lock"])
    print(f"independent lock check: {'OK' if not errors else errors}")
    assert not errors
    assert "unused" not in result["lock"]["packages"]
    print("note: web 2.0.0 was tried first and rolled back; "
          "'unused' correctly stayed out of the lock")

    print()
    print("=" * 72)
    print("CASE 2: real failure - diamond conflict on 'shared' (unsat)")
    print("=" * 72)
    result = solve(CONFLICT_REGISTRY, CONFLICT_REQUESTS, budget=1000)
    print(f"status: {result['status']}  (nodes used: {result['nodes_used']})")
    print(f"roots: {result['roots']}")
    for conflict in result["conflicts"]:
        print(f"  conflict on {conflict['package']!r}: {conflict['reason']}")
        for source in conflict["constraint_sources"]:
            print(f"    constraint from: {source}")

    print()
    print("=" * 72)
    print("CASE 2b: same solvable registry as case 1, but budget = 2 (unknown)")
    print("=" * 72)
    result = solve(REGISTRY, REQUESTS, budget=2)
    print(f"status: {result['status']}  reason: {result['reason']}")
    print("(budget exhaustion is reported as unknown, never as unsat)")


if __name__ == "__main__":
    main()
