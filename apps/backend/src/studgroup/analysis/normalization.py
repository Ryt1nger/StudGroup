"""Fact-preserving deterministic normalization after strict validation."""

from studgroup.analysis.contracts import EvidenceRef, ValidationResult


def normalize_validation(result: ValidationResult) -> ValidationResult:
    normalized = []
    for proposal in result.accepted:
        evidence_by_source: dict[tuple[int, int], list[str]] = {}
        for evidence in proposal.evidence:
            key = (evidence.telegram_message_id, evidence.revision)
            paths = evidence_by_source.setdefault(key, [])
            paths.extend(path for path in evidence.fact_paths if path not in paths)
        evidence = [
            EvidenceRef(
                telegram_message_id=message_id,
                revision=revision,
                fact_paths=paths,
            )
            for (message_id, revision), paths in evidence_by_source.items()
        ]
        normalized.append(
            proposal.model_copy(
                update={
                    "attention_reasons": list(dict.fromkeys(proposal.attention_reasons)),
                    "evidence": evidence,
                }
            )
        )
    return result.model_copy(update={"accepted": normalized})
