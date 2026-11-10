import type { EventPayload } from '@studgroup/shared-types';

/** One safe editable payload shape, shared by the event review form and calendar date dialog. */
export function eventPayload(item: EventPayload): EventPayload {
  return {
    title: item.title,
    description: item.description ?? '',
    starts_at: item.starts_at ?? null,
    ends_at: item.ends_at ?? null,
    starts_on: item.starts_on ?? null,
    ends_on: item.ends_on ?? null,
    multi_day: item.multi_day ?? false,
    when_text: item.when_text ?? null,
    place: item.place ?? null,
    conditions: item.conditions ?? null,
    audience: item.audience ?? 'unknown',
    audience_label: item.audience_label ?? null,
  };
}
