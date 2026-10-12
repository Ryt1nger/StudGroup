"""Versioned, side-effect-free contracts for the C+ analysis engine."""

from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    AnalysisInput,
    AnalysisOutput,
    ApplicationOutcome,
    EvidenceRef,
    ProposedAcademicItem,
    ValidationResult,
)

__all__ = [
    "ANALYSIS_ENGINE_VERSION",
    "ANALYSIS_SCHEMA_VERSION",
    "AnalysisInput",
    "AnalysisOutput",
    "ApplicationOutcome",
    "EvidenceRef",
    "ProposedAcademicItem",
    "ValidationResult",
]
