"""Domain errors.

Adapters catch vendor exceptions and re-raise these. A vendor error type
reaching `core/` would be an inward dependency through the exception, which is
the same leak as importing the SDK.
"""

from __future__ import annotations


class DssError(Exception):
    """Base for anything the DSS itself raises."""


class ProviderUnavailable(DssError):
    """A dependency the turn needed could not be reached."""
