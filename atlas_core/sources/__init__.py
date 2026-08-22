"""Secure, provider-owned source acquisition primitives."""

from .contracts import SourceObservation, SourceRef
from .capabilities import register_files_capabilities
from .errors import LocalSourceError
from .local import (
    CancellationToken,
    LocalRootConfig,
    LocalRootRegistry,
    LocalSourceKernel,
    SourceListResult,
    SourceReadResult,
)

__all__ = [
    "CancellationToken",
    "LocalRootConfig",
    "LocalRootRegistry",
    "LocalSourceError",
    "LocalSourceKernel",
    "SourceListResult",
    "SourceObservation",
    "SourceReadResult",
    "SourceRef",
    "register_files_capabilities",
]
