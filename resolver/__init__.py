"""Offline dependency resolver: registry model, backtracking solver,
independent lock checker, and a brute-force enumerator for cross-validation.
"""

from .brute import brute_force_locks
from .checker import check_lock
from .model import (
    MAX_PACKAGES,
    MAX_VERSIONS_PER_PACKAGE,
    Registry,
    RegistryError,
    Request,
    load_registry,
    load_requests,
)
from .solver import solve

__all__ = [
    "MAX_PACKAGES",
    "MAX_VERSIONS_PER_PACKAGE",
    "Registry",
    "RegistryError",
    "Request",
    "brute_force_locks",
    "check_lock",
    "load_registry",
    "load_requests",
    "solve",
]
