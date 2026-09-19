"""Registry and request data models with load-time validation."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import (
    DuplicatePackageError,
    DuplicateRootIdError,
    DuplicateVersionError,
    InvalidInputError,
    UnknownPackageError,
)
from .intervals import ANY, Constraint, Version, as_version, make_constraint

MAX_PACKAGES = 12
MAX_VERSIONS = 6


def parse_constraint(value) -> Constraint:
    """Parse a union of intervals given as ``[[low, high], ...]``.

    An omitted key (or ``None``) means "any version"; an explicit empty list
    is the empty union and permits no version at all.
    """
    if value is None:
        return list(ANY)
    if not isinstance(value, (list, tuple)):
        raise InvalidInputError(f"constraint must be a list of intervals, got {value!r}")
    return make_constraint(value)


@dataclass(frozen=True)
class FeatureDef:
    """A named feature of one concrete package version."""

    name: str
    # package name -> union of allowed version intervals
    dependencies: dict[str, Constraint] = field(default_factory=dict)
    # package name -> feature names requested on that package
    feature_requests: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class PackageVersion:
    version: Version
    dependencies: dict[str, Constraint]
    features: dict[str, FeatureDef]


@dataclass(frozen=True)
class Package:
    name: str
    # versions kept newest-first; tuple preserves the deterministic order
    versions: tuple[PackageVersion, ...]

    def version_map(self) -> dict[Version, PackageVersion]:
        return {v.version: v for v in self.versions}


@dataclass(frozen=True)
class Registry:
    packages: dict[str, Package]

    def get(self, name: str) -> Package:
        return self.packages[name]

    def __contains__(self, name: str) -> bool:
        return name in self.packages


@dataclass(frozen=True)
class RootRequest:
    root_id: str
    # package name -> required version union
    requirements: dict[str, Constraint]
    # package name -> feature names initially requested on it
    feature_requests: dict[str, tuple[str, ...]]


def _require_name(value, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidInputError(f"{what} must be a non-empty string, got {value!r}")
    return value


def build_registry(data) -> Registry:
    """Validate dictionary-shaped registry input and build a :class:`Registry`.

    Rejects duplicate package names, duplicate versions, oversized tables
    (more than 12 packages or 6 versions per package), and dependency / feature
    references to packages that do not exist.
    """
    if not isinstance(data, dict) or not isinstance(data.get("packages"), list):
        raise InvalidInputError("registry must be a dict with a 'packages' list")
    if len(data["packages"]) == 0:
        raise InvalidInputError("registry must contain at least one package")
    if len(data["packages"]) > MAX_PACKAGES:
        raise InvalidInputError(
            f"registry has {len(data['packages'])} packages; at most {MAX_PACKAGES} are supported"
        )

    packages: dict[str, Package] = {}
    seen_versions: dict[str, set[Version]] = {}
    for entry in data["packages"]:
        if not isinstance(entry, dict):
            raise InvalidInputError(f"package entry must be a dict, got {entry!r}")
        name = _require_name(entry.get("name"), "package name")
        if name in packages:
            raise DuplicatePackageError(f"duplicate package name: {name!r}")
        raw_versions = entry.get("versions", [])
        if not isinstance(raw_versions, list) or not raw_versions:
            raise InvalidInputError(f"package {name!r} must declare a non-empty versions list")
        if len(raw_versions) > MAX_VERSIONS:
            raise InvalidInputError(
                f"package {name!r} has {len(raw_versions)} versions; at most {MAX_VERSIONS}"
            )

        versions: list[PackageVersion] = []
        seen: set[Version] = set()
        for raw_v in raw_versions:
            if not isinstance(raw_v, dict):
                raise InvalidInputError(f"version entry in {name!r} must be a dict")
            version = as_version(raw_v["version"])
            if version in seen:
                raise DuplicateVersionError(
                    f"duplicate version {version} in package {name!r}"
                )
            seen.add(version)

            deps = _parse_dependencies(raw_v.get("dependencies"), f"package {name} {version}")
            features = _parse_features(raw_v.get("features"), f"package {name} {version}")
            versions.append(PackageVersion(version, deps, features))
        seen_versions[name] = seen
        versions.sort(key=lambda pv: pv.version, reverse=True)
        packages[name] = Package(name, tuple(versions))

    # Reference validation: every dependency / feature-request target exists.
    for pkg in packages.values():
        for pv in pkg.versions:
            for dep in pv.dependencies:
                if dep not in packages:
                    raise UnknownPackageError(
                        f"{pkg.name} {pv.version} depends on unknown package {dep!r}"
                    )
            for feat in pv.features.values():
                for dep in feat.dependencies:
                    if dep not in packages:
                        raise UnknownPackageError(
                            f"feature {feat.name!r} of {pkg.name} {pv.version} "
                            f"depends on unknown package {dep!r}"
                        )
                for target in feat.feature_requests:
                    if target not in packages:
                        raise UnknownPackageError(
                            f"feature {feat.name!r} of {pkg.name} {pv.version} "
                            f"requests features on unknown package {target!r}"
                        )
    return Registry(packages)


def _parse_dependencies(raw, where: str) -> dict[str, Constraint]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InvalidInputError(f"dependencies of {where} must be a mapping")
    out: dict[str, Constraint] = {}
    for dep, ranges in raw.items():
        _require_name(dep, "dependency name")
        out[dep] = parse_constraint(ranges)
    return out


def _parse_features(raw, where: str) -> dict[str, FeatureDef]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InvalidInputError(f"features of {where} must be a mapping")
    out: dict[str, FeatureDef] = {}
    for fname, fdef in raw.items():
        _require_name(fname, "feature name")
        if not isinstance(fdef, dict):
            raise InvalidInputError(f"feature {fname!r} of {where} must be a mapping")
        deps = _parse_dependencies(fdef.get("dependencies"), f"feature {fname} of {where}")
        requests = _parse_feature_requests(
            fdef.get("feature_requests"), f"feature {fname} of {where}"
        )
        out[fname] = FeatureDef(fname, deps, requests)
    return out


def _parse_feature_requests(raw, where: str) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InvalidInputError(f"feature_requests of {where} must be a mapping")
    out: dict[str, tuple[str, ...]] = {}
    for target, feats in raw.items():
        _require_name(target, "feature-request target package")
        if not isinstance(feats, (list, tuple)) or not feats:
            raise InvalidInputError(f"feature requests for {target!r} of {where} must be a non-empty list")
        names: list[str] = []
        for f in feats:
            names.append(_require_name(f, "feature name"))
        out[target] = tuple(dict.fromkeys(names))
    return out


def build_request(data, registry: Registry | None = None) -> RootRequest:
    """Validate a root request. When a registry is given, referenced packages
    must exist in it."""
    if not isinstance(data, dict):
        raise InvalidInputError("request must be a dict")
    root_id = _require_name(data.get("id"), "root id")
    requirements = _parse_dependencies(data.get("requirements"), f"root {root_id!r}")
    requests = _parse_feature_requests(data.get("feature_requests"), f"root {root_id!r}")
    if registry is not None:
        for dep in requirements:
            if dep not in registry:
                raise UnknownPackageError(
                    f"root {root_id!r} requires unknown package {dep!r}"
                )
        for target in requests:
            if target not in registry:
                raise UnknownPackageError(
                    f"root {root_id!r} requests features on unknown package {target!r}"
                )
    return RootRequest(root_id, requirements, requests)


def ensure_unique_root_ids(requests: list[RootRequest]) -> None:
    seen: set[str] = set()
    for req in requests:
        if req.root_id in seen:
            raise DuplicateRootIdError(f"duplicate root id: {req.root_id!r}")
        seen.add(req.root_id)
