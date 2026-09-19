"""Version triples, half-open version intervals, and their unions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .errors import InvalidInputError

Version = tuple[int, int, int]
# A constraint is a union of disjoint, sorted, half-open [low, high) ranges.
Constraint = list[tuple[Version, Version]]

MAX_VERSION: Version = (10**9, 10**9, 10**9)
# The unconstrained union: a single half-open range covering every triple.
ANY: Constraint = [((0, 0, 0), MAX_VERSION)]


def to_json(constraint: Constraint) -> list[list[list[int]]]:
    """Serialize a constraint to plain JSON-compatible data."""
    return [[list(lo), list(hi)] for lo, hi in constraint]


def as_version(value: Sequence[int]) -> Version:
    """Validate ``value`` as a non-negative integer triple and return a tuple."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise InvalidInputError(
            f"version must be a triple of non-negative ints, got {value!r}"
        )
    out = list(value)
    for part in out:
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            raise InvalidInputError(
                f"version components must be non-negative ints, got {value!r}"
            )
    return (out[0], out[1], out[2])


def _merge(ranges: Iterable[tuple[Version, Version]]) -> Constraint:
    """Merge possibly overlapping half-open ranges into a normalized union."""
    items = sorted(ranges)
    merged: Constraint = []
    for low, high in items:
        if low >= high:
            continue
        if merged and low <= merged[-1][1]:
            if high > merged[-1][1]:
                merged[-1] = (merged[-1][0], high)
        else:
            merged.append((low, high))
    return merged


def make_constraint(ranges: Iterable[Sequence[Version]]) -> Constraint:
    """Build a normalized union of [low, high) ranges."""
    norm: list[tuple[Version, Version]] = []
    for rng in ranges:
        if not isinstance(rng, (list, tuple)) or len(rng) != 2:
            raise InvalidInputError(f"interval must be a [low, high) pair, got {rng!r}")
        low, high = as_version(rng[0]), as_version(rng[1])
        if low >= high:
            raise InvalidInputError(
                f"interval must be non-empty with low < high, got {rng!r}"
            )
        norm.append((low, high))
    return _merge(norm)


def intersect(a: Constraint, b: Constraint) -> Constraint:
    """Intersect two unions of half-open intervals."""
    out: list[tuple[Version, Version]] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo_a, hi_a = a[i]
        lo_b, hi_b = b[j]
        low = max(lo_a, lo_b)
        high = min(hi_a, hi_b)
        if low < high:
            out.append((low, high))
        if hi_a < hi_b:
            i += 1
        elif hi_b < hi_a:
            j += 1
        else:
            i += 1
            j += 1
    return out


def contains(constraint: Constraint, version: Version) -> bool:
    """Return whether ``version`` lies in the union (half-open high bounds)."""
    for low, high in constraint:
        if low <= version < high:
            return True
    return False


def is_empty(constraint: Constraint) -> bool:
    return not constraint
