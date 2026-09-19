"""Independent lockfile checker.

Given a registry, the original root request and a produced lock, the checker
re-derives every requirement from scratch instead of trusting solver state:

* every locked version exists in the registry and satisfies the root
  requirements and the mandatory dependencies of its selected version;
* the enabled-feature set is recomputed as a least fixed point (root requests
  plus feature-added dependencies and feature requests, cycles allowed) and
  must equal the lock's feature set exactly;
* the locked package set must be exactly the root/activated-dependency
  closure, so unrelated registry packages cannot appear.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .intervals import Version, contains
from .model import Registry, RootRequest


@dataclass
class LockCheck:
    ok: bool
    errors: list[str] = field(default_factory=list)

    def raise_if_invalid(self) -> None:
        if not self.ok:
            raise ValueError("invalid lock:\n  - " + "\n  - ".join(self.errors))


def _version(value) -> Version:
    return (int(value[0]), int(value[1]), int(value[2]))


def check_lock(registry: Registry, request: RootRequest, lock: dict) -> LockCheck:
    """Independently verify a sat lock produced for ``request``."""
    errors: list[str] = []

    def add_error(msg: str) -> None:
        if msg not in errors:
            errors.append(msg)

    raw_versions = lock.get("versions")
    raw_features = lock.get("features", {})
    sources = lock.get("dependency_sources", {})
    if not isinstance(raw_versions, dict):
        return LockCheck(False, ["lock 'versions' must be a mapping"])
    if not isinstance(raw_features, dict):
        errors.append("lock 'features' must be a mapping")
        raw_features = {}

    versions: dict[str, Version] = {}
    selected: dict[str, object] = {}
    for pkg, raw_v in raw_versions.items():
        if pkg not in registry:
            add_error(f"locked package {pkg!r} is not in the registry")
            continue
        try:
            v = _version(raw_v)
        except (TypeError, ValueError, IndexError):
            add_error(f"locked version for {pkg!r} is not a version triple: {raw_v!r}")
            continue
        pv = registry.get(pkg).version_map().get(v)
        if pv is None:
            add_error(f"locked version {v} of {pkg!r} does not exist in the registry")
            continue
        versions[pkg] = v
        selected[pkg] = pv

    enabled: dict[str, set[str]] = {}
    for pkg, feats in raw_features.items():
        if pkg not in versions:
            add_error(f"features listed for package not in the lock: {pkg!r}")
            continue
        if not isinstance(feats, list) or any(not isinstance(f, str) for f in feats):
            add_error(f"features for {pkg!r} must be a list of strings")
            continue
        if len(set(feats)) != len(feats):
            add_error(f"duplicate features listed for {pkg!r}: {feats}")
        enabled[pkg] = set(feats)

    def check_dep(owner: str, dep: str, constraint) -> None:
        if dep not in versions:
            add_error(f"dependency {dep!r} required by {owner} is missing from the lock")
        elif not contains(constraint, versions[dep]):
            add_error(
                f"locked {dep}=={versions[dep]} violates constraint from {owner}: {constraint}"
            )

    # --- Root requirements ------------------------------------------------
    for pkg, constraint in request.requirements.items():
        if pkg not in versions:
            add_error(f"root-required package {pkg!r} is missing from the lock")
        else:
            check_dep(f"root {request.root_id!r}", pkg, constraint)

    # --- Mandatory dependencies -------------------------------------------
    for pkg, pv in selected.items():
        for dep, constraint in pv.dependencies.items():
            check_dep(f"{pkg}=={versions[pkg]} mandatory", dep, constraint)

    # --- Feature least fixed point (cycles allowed) -----------------------
    fixpoint: dict[str, set[str]] = {}

    def enable(pkg: str, feat: str) -> bool:
        bucket = fixpoint.setdefault(pkg, set())
        if feat in bucket:
            return False
        bucket.add(feat)
        return True

    for target, feats in request.feature_requests.items():
        if target not in versions:
            add_error(
                f"root requests features on package {target!r} missing from the lock"
            )
            continue
        for f in feats:
            enable(target, f)

    pending = True
    while pending:
        pending = False
        for pkg in list(fixpoint):
            pv = selected.get(pkg)
            if pv is None:
                continue
            for feat in list(fixpoint[pkg]):
                if feat not in pv.features:
                    add_error(
                        f"feature {feat!r} does not exist on {pkg}=={versions.get(pkg)}"
                    )
                    continue
                fdef = pv.features[feat]
                for target, feats in fdef.feature_requests.items():
                    if target not in versions:
                        add_error(
                            f"{pkg}=={versions[pkg]} feature {feat!r} requests features "
                            f"on {target!r}, missing from the lock"
                        )
                        continue
                    for f in feats:
                        pending |= enable(target, f)

    # Feature-added dependencies checked once the fixed point has converged.
    for pkg, feats in fixpoint.items():
        pv = selected.get(pkg)
        if pv is None:
            continue
        for feat in feats:
            fdef = pv.features.get(feat)
            if fdef is None:
                continue
            owner = f"{pkg}=={versions[pkg]} feature {feat}"
            for dep, constraint in fdef.dependencies.items():
                check_dep(owner, dep, constraint)

    if not errors:
        for pkg, pv in selected.items():
            expected = fixpoint.get(pkg, set())
            actual = enabled.get(pkg, set())
            unknown = actual - set(pv.features)
            if unknown:
                add_error(
                    f"{pkg}=={versions[pkg]} enables undefined features: {sorted(unknown)}"
                )
            if actual != expected:
                add_error(
                    f"feature set for {pkg}=={versions[pkg]} is not the fixed point: "
                    f"lock={sorted(actual)} expected={sorted(expected)}"
                )

    # --- Exact reachability closure (no unrelated packages) ---------------
    if not errors:
        reachable: set[str] = set(request.requirements) | set(request.feature_requests)
        grown = True
        while grown:
            grown = False
            for pkg in list(reachable):
                pv = selected.get(pkg)
                if pv is None:
                    continue
                for dep in pv.dependencies:
                    if dep not in reachable:
                        reachable.add(dep)
                        grown = True
                for feat in fixpoint.get(pkg, ()):
                    fdef = pv.features.get(feat)
                    if fdef is None:
                        continue
                    for dep in fdef.dependencies:
                        if dep not in reachable:
                            reachable.add(dep)
                            grown = True
                    for target in fdef.feature_requests:
                        if target not in reachable:
                            reachable.add(target)
                            grown = True
        for pkg in sorted(reachable - set(versions)):
            add_error(f"package {pkg!r} is reachable but missing from the lock")
        for pkg in sorted(set(versions) - reachable):
            add_error(f"package {pkg!r} is in the lock but not reachable from the root")

    # --- Light source provenance check ------------------------------------
    for pkg in versions:
        srcs = sources.get(pkg)
        root_required = pkg in request.requirements or pkg in request.feature_requests
        if root_required:
            if not isinstance(srcs, list) or request.root_id not in srcs:
                add_error(f"root package {pkg!r} source list must include the root id")
        elif not srcs:
            add_error(f"package {pkg!r} has no recorded dependency source")

    return LockCheck(not errors, errors)
