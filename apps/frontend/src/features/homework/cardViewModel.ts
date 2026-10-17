import type {
  HomeworkDetail,
  LearningCardKind,
  MaterialLink,
  SourceDetail,
  SubjectRef,
} from '@studgroup/shared-types';
import { confirmedSubject } from './confirmedSubject';

export type DeadlineCertainty = 'confirmed' | 'from_source' | 'inferred' | 'needs_clarification' | 'unknown';
export type LearningCardUpdateKind = 'supplement' | 'change' | 'cancellation';

export interface LearningCardUpdateView {
  id: string;
  kind: LearningCardUpdateKind;
  text: string;
  createdAt: string | null;
}

export interface HomeworkCardViewModel {
  kind: LearningCardKind;
  subject: SubjectRef | null;
  deadlineCertainty: DeadlineCertainty;
  materials: MaterialLink[] | undefined;
  sources: SourceDetail[];
  updates: LearningCardUpdateView[];
}

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord | null {
  return typeof value === 'object' && value !== null ? (value as UnknownRecord) : null;
}

function learningCardKind(value: unknown): LearningCardKind {
  switch (value) {
    case 'control_point':
    case 'assessment':
    case 'test':
    case 'credit':
    case 'exam':
    case 'other_academic_deadline':
      return value;
    default:
      return 'homework';
  }
}

function deadlineCertainty(detail: HomeworkDetail, extension: UnknownRecord): DeadlineCertainty {
  if (detail.deadline.certainty) return detail.deadline.certainty;
  const deadline = record(extension.deadline);
  const explicit = deadline?.certainty;
  if (
    explicit === 'confirmed' ||
    explicit === 'from_source' ||
    explicit === 'inferred' ||
    explicit === 'needs_clarification'
  ) {
    return explicit;
  }
  if (detail.deadline.state === 'unknown' || !detail.deadline.at) return 'unknown';
  if (detail.status === 'needs_clarification' || detail.verification_state === 'needs_clarification') {
    return 'needs_clarification';
  }
  if (detail.verification_state === 'manual_confirmed') return 'confirmed';
  if (detail.verification_state === 'from_group_message') return 'from_source';
  if (detail.verification_state === 'inferred') return 'inferred';
  return 'unknown';
}

function isSourceDetail(value: unknown): value is SourceDetail {
  const source = record(value);
  const availability = record(source?.availability);
  const action = record(source?.action);
  return (
    typeof source?.kind === 'string' &&
    typeof source.imported === 'boolean' &&
    (availability?.state === 'available' || availability?.state === 'unavailable') &&
    (action?.type === 'open_telegram_link' || action?.type === 'none')
  );
}

function sources(detail: HomeworkDetail, extension: UnknownRecord): SourceDetail[] {
  if (detail.sources?.length) return detail.sources;
  if (Array.isArray(extension.sources)) {
    const compatible = extension.sources.filter(isSourceDetail);
    if (compatible.length) return compatible;
  }
  return [detail.source_detail];
}

function updates(extension: UnknownRecord): LearningCardUpdateView[] {
  if (!Array.isArray(extension.updates)) return [];
  return extension.updates.flatMap((value, index) => {
    const item = record(value);
    if (!item) return [];
    const kind = item?.kind;
    if (
      (kind !== 'supplement' && kind !== 'change' && kind !== 'cancellation') ||
      typeof item.text !== 'string' ||
      !item.text.trim()
    ) {
      return [];
    }
    return [
      {
        id: typeof item.id === 'string' ? item.id : `update-${index}`,
        kind,
        text: item.text.trim(),
        createdAt: typeof item.created_at === 'string' ? item.created_at : null,
      },
    ];
  });
}

/**
 * Presentation-only compatibility boundary. The OpenAPI HomeworkDetail remains the source of truth.
 * Optional extension fields are ignored unless they match the documented readiness proposal.
 */
export function toHomeworkCardViewModel(detail: HomeworkDetail): HomeworkCardViewModel {
  const extension = detail as unknown as UnknownRecord;
  return {
    kind: learningCardKind(detail.card_kind ?? extension.card_kind),
    subject: confirmedSubject(detail),
    deadlineCertainty: deadlineCertainty(detail, extension),
    materials: detail.materials,
    sources: sources(detail, extension),
    updates: updates(extension),
  };
}
