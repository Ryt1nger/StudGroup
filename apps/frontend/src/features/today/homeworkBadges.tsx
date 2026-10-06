import type { HomeworkSummary } from '@studgroup/shared-types';
import type { ReactNode } from 'react';
import { ru } from '../../i18n/ru';
import { Badge } from '../../ui/Badge';
import { AlertCircle, CheckCircle, Clock, Refresh, Sparkles } from '../../ui/icons';

/**
 * Badge semantics follow the contract and owner decisions. Values whose copy is not settled
 * (`inferred`, `imported` verification, group `completed`) intentionally render nothing.
 */
export function verificationBadge(item: Pick<HomeworkSummary, 'verification_state' | 'status'>): ReactNode {
  if (item.status === 'needs_clarification' || item.verification_state === 'needs_clarification') {
    return (
      <Badge tone="warning" icon={<AlertCircle />}>
        {ru.card.needsClarification}
      </Badge>
    );
  }
  if (item.verification_state === 'manual_confirmed') {
    return (
      <Badge tone="success" icon={<CheckCircle />}>
        {ru.card.verified}
      </Badge>
    );
  }
  if (item.verification_state === 'from_group_message') {
    return (
      <Badge tone="info" icon={<Sparkles />}>
        {ru.card.fromGroupMessage}
      </Badge>
    );
  }
  return null;
}

/** Red styling and «Срочно» come only from the backend `urgency` field. */
export function urgencyBadge(urgency: HomeworkSummary['urgency']): ReactNode {
  if (urgency === 'normal') return null;
  return (
    <Badge tone="danger" icon={<AlertCircle />}>
      {ru.card.urgent}
    </Badge>
  );
}

export function sourceLabel(source: HomeworkSummary['source']): string | null {
  if (source.imported || source.kind === 'imported_history') return ru.card.sourceImported;
  if (source.kind === 'telegram_group_message') return ru.card.sourceGroup;
  return null; // direct message / manual entry: label not defined yet (contract review З-2)
}

export function processingBadge(state: HomeworkSummary['processing']['state']): ReactNode {
  if (state === 'reanalyzing') {
    return (
      <Badge tone="info" icon={<Refresh />}>
        {ru.card.reanalyzing}
      </Badge>
    );
  }
  if (state === 'delayed') {
    return (
      <Badge tone="warning" icon={<Clock />}>
        {ru.card.delayed}
      </Badge>
    );
  }
  return null;
}
