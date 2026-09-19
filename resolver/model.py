"""Data model, validation and range algebra for the offline resolver.

Raw inputs are plain JSON-style dicts so registries and requests are easy to
write in tests and demos; this module validates and normalizes them into
immutable structures used by the solver, checker and brute-force enumerator.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_PACKAGES = 12
MAX_VERSIONS_PER_PACKAGE = 6

Version = tuple  # tuple[int, int, int]
# A range set is a union of half-open [lo, hi) intervals; hi=None means +inf.
RangeSet = tuple  # tuple[tuple[Version, Version | None], ...]

ANY_RANGES: RangeSet = (((0, 0, 0), None),)


class RegistryError(ValueError):
    """Raised when a registry or root request list is malformed."""


def parse_version(value, where: str) -> Version:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(type(part) is int and part >= 0 for part in value)
    ):
        return tuple(value)
    raise RegistryError(
        f"{where}: version must be a triple of non-negative integers, got {value!r}"
    )


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def _range_sort_key(interval):
    lo, hi = interval
    return (lo, hi is None, hi if hi is not None else (0, 0, 0))


def parse_ranges(raw, where: str) -> RangeSet:
    """Normalize a raw ranges field into a RangeSet.

    Raw form: None (any) or a list of [lo, hi] pairs; lo/hi are version
    triples, null lo means 0.0.0, null hi means unbounded. Empty intervals
    (lo >= hi) are dropped; an empty list therefore matches nothing.
    """
    if raw is None:
        return ANY_RANGES
    if not isinstance(raw, (list, tuple)):
        raise RegistryError(f"{where}: ranges must be a list of [lo, hi] pairs")
    out = []
    for i, item in enumerate(raw):
        entry_where = f"{where} range #{i}"
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise RegistryError(f"{entry_where}: expected a [lo, hi] pair, got {item!r}")
        lo_raw, hi_raw = item
        lo = (0, 0, 0) if lo_raw is None else parse_version(lo_raw, f"{entry_where} lo")
        hi = None if hi_raw is None else parse_version(hi_raw, f"{entry_where} hi")
        if hi is None or lo < hi:
            out.append((lo, hi))
        # lo >= hi: empty interval, contributes nothing to the union.
    out.sort(key=_range_sort_key)
    return tuple(out)


def ranges_intersect(a: RangeSet, b: RangeSet) -> RangeSet:
    out = []
    for lo1, hi1 in a:
        for lo2, hi2 in b:
            lo = max(lo1, lo2)
            if hi1 is None:
                hi = hi2
            elif hi2 is None:
                hi = hi1
            else:
                hi = min(hi1, hi2)
            if hi is None or lo < hi:
                out.append((lo, hi))
    out.sort(key=_range_sort_key)
    return tuple(out)


def ranges_contains(ranges: RangeSet, version: Version) -> bool:
    return any(lo <= version and (hi is None or version < hi) for lo, hi in ranges)


def ranges_empty(ranges: RangeSet) -> bool:
    return len(ranges) == 0


def format_ranges(ranges: RangeSet) -> list:
    return [[list(lo), list(hi) if hi is not None else None] for lo, hi in ranges]


@dataclass(frozen=True)
class Dep:
    package: str
    ranges: RangeSet
    features: frozenset


@dataclass(frozen=True)
class VersionInfo:
    version: Version
    deps: tuple  # tuple[Dep, ...] required dependencies
    features: dict  # dict[str, tuple[Dep, ...]] named optional features


@dataclass(frozen=True)
class PackageInfo:
    name: str
    versions: dict  # dict[Version, VersionInfo]


@dataclass(frozen=True)
class Registry:
    packages: dict  # dict[str, PackageInfo]


@dataclass(frozen=True)
class Request:
    id: str
    package: str
    ranges: RangeSet
    features: frozenset


def _parse_name(raw, where: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise RegistryError(f"{where}: expected a non-empty name string, got {raw!r}")
    return raw


def _parse_feature_names(raw, where: str) -> frozenset:
    if raw is None:
        return frozenset()
    if not isinstance(raw, (list, tuple)) or not all(
        isinstance(f, str) and f for f in raw
    ):
        raise RegistryError(f"{where}: features must be a list of non-empty strings")
    return frozenset(raw)


def _parse_dep(raw, where: str, known_packages: set) -> Dep:
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: dependency must be an object, got {raw!r}")
    pkg = _parse_name(raw.get("package"), where)
    if pkg not in known_packages:
        raise RegistryError(f"{where}: reference to unknown package {pkg!r}")
    return Dep(
        package=pkg,
        ranges=parse_ranges(raw.get("ranges"), where),
        features=_parse_feature_names(raw.get("features"), where),
    )


def load_registry(raw) -> Registry:
    """Validate and normalize a raw registry (list of package objects)."""
    if not isinstance(raw, (list, tuple)):
        raise RegistryError("registry must be a list of package objects")
    if not raw:
        raise RegistryError("registry must contain at least one package")
    if len(raw) > MAX_PACKAGES:
        raise RegistryError(f"registry has {len(raw)} packages, max is {MAX_PACKAGES}")

    names = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise RegistryError(f"package #{i}: must be an object, got {entry!r}")
        names.append(_parse_name(entry.get("name"), f"package #{i}"))
    if len(set(names)) != len(names):
        raise RegistryError(f"duplicate package names: {sorted(names)}")
    known = set(names)

    packages = {}
    for name, entry in zip(names, raw):
        where = f"package {name!r}"
        versions_raw = entry.get("versions")
        if not isinstance(versions_raw, (list, tuple)) or not versions_raw:
            raise RegistryError(f"{where}: versions must be a non-empty list")
        if len(versions_raw) > MAX_VERSIONS_PER_PACKAGE:
            raise RegistryError(
                f"{where}: {len(versions_raw)} versions, max is {MAX_VERSIONS_PER_PACKAGE}"
            )
        versions = {}
        for j, vraw in enumerate(versions_raw):
            vwhere = f"{where} version #{j}"
            if not isinstance(vraw, dict):
                raise RegistryError(f"{vwhere}: must be an object, got {vraw!r}")
            ver = parse_version(vraw.get("version"), vwhere)
            if ver in versions:
                raise RegistryError(f"{where}: duplicate version {format_version(ver)}")
            deps_raw = vraw.get("deps") or []
            if not isinstance(deps_raw, (list, tuple)):
                raise RegistryError(f"{vwhere}: deps must be a list")
            deps = tuple(
                _parse_dep(d, f"{vwhere} dep #{k}", known)
                for k, d in enumerate(deps_raw)
            )
            features_raw = vraw.get("features") or {}
            if not isinstance(features_raw, dict):
                raise RegistryError(f"{vwhere}: features must be an object")
            features = {}
            for fname, fdeps_raw in features_raw.items():
                _parse_name(fname, f"{vwhere} feature")
                if not isinstance(fdeps_raw, (list, tuple)):
                    raise RegistryError(f"{vwhere} feature {fname!r}: deps must be a list")
                features[fname] = tuple(
                    _parse_dep(d, f"{vwhere} feature {fname!r} dep #{k}", known)
                    for k, d in enumerate(fdeps_raw)
                )
            versions[ver] = VersionInfo(version=ver, deps=deps, features=features)
        packages[name] = PackageInfo(name=name, versions=versions)
    return Registry(packages=packages)


def load_requests(raw, registry: Registry) -> tuple:
    """Validate and normalize raw root requests; each id must be unique."""
    if not isinstance(raw, (list, tuple)) or not raw:
        raise RegistryError("requests must be a non-empty list")
    requests = []
    seen_ids = set()
    for i, entry in enumerate(raw):
        where = f"request #{i}"
        if not isinstance(entry, dict):
            raise RegistryError(f"{where}: must be an object, got {entry!r}")
        rid = _parse_name(entry.get("id"), where)
        if rid in seen_ids:
            raise RegistryError(f"duplicate root request id {rid!r}")
        seen_ids.add(rid)
        pkg = _parse_name(entry.get("package"), where)
        if pkg not in registry.packages:
            raise RegistryError(f"{where}: reference to unknown package {pkg!r}")
        requests.append(
            Request(
                id=rid,
                package=pkg,
                ranges=parse_ranges(entry.get("ranges"), where),
                features=_parse_feature_names(entry.get("features"), where),
            )
        )
    return tuple(requests)
