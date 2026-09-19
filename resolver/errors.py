"""Validation errors for malformed registries and requests."""

from __future__ import annotations


class ResolverError(ValueError):
    """Base class for all input-validation errors raised by the library."""


class DuplicatePackageError(ResolverError):
    """A package is declared more than once in the registry."""


class DuplicateVersionError(ResolverError):
    """A package declares the same version triple more than once."""


class DuplicateRootIdError(ResolverError):
    """A batch contains more than one request with the same root id."""


class UnknownPackageError(ResolverError):
    """A dependency or feature edge names a package absent from the registry."""


class InvalidInputError(ResolverError):
    """An input value has the wrong shape or falls outside its allowed range."""
