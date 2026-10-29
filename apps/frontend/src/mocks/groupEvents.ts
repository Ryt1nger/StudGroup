/** Development-only event journey; no Telegram, AI or production writes. */
import type { EventReview, ReviewImageEventData } from '@studgroup/shared-types';
import { http, HttpResponse } from 'msw';
import { addDays, dayKey } from '../lib/format';
import { eventDays, eventInHistory } from '../features/events/calendar';

export function eventHandlers(baseUrl: string) {
  const today = dayKey(Date.now(), 'Europe/Moscow');
  let item: EventReview = {
    id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    revision: 1,
    state: 'draft',
    title: 'Открытая лекция о космосе',
    description: 'Обсудим исследования Луны.',
    starts_on: addDays(today, 1),
    ends_on: null,
    starts_at: null,
    ends_at: null,
    place: 'Актовый зал',
    conditions: 'Вход свободный',
    audience: 'group',
    manual_lock: false,
    source_availability: 'available',
    source_url: 'https://t.me/c/123456/31',
    findings: [],
    retained_until: `${addDays(today, 10)}T00:00:00+03:00`,
  };
  return [
    http.get(`${baseUrl}/staff/image-findings/events`, () => HttpResponse.json({ events: [item] })),
    http.put(`${baseUrl}/staff/image-findings/events/:id`, async ({ request, params }) => {
      const body = (await request.json()) as ReviewImageEventData['body'];
      if (params.id !== item.id) return HttpResponse.json({}, { status: 404 });
      if (body.expected_revision !== item.revision) return HttpResponse.json({}, { status: 409 });
      const before = eventDays(item, 'Europe/Moscow');
      const after = eventDays(body.payload, 'Europe/Moscow');
      item = {
        ...item,
        ...body.payload,
        state: body.state ?? 'draft',
        revision: item.revision + 1,
        manual_lock: true,
        rescheduled:
          item.rescheduled || (item.state !== 'draft' && JSON.stringify(before) !== JSON.stringify(after)),
      };
      return HttpResponse.json(item);
    }),
    http.get(`${baseUrl}/group-events`, ({ request }) => {
      const query = new URL(request.url).searchParams;
      const start = query.get('start');
      const end = query.get('end');
      const days = eventDays(item, 'Europe/Moscow');
      const status = query.get('status') ?? 'active';
      const visible =
        item.state !== 'draft' &&
        (status === 'all' || item.state === status) &&
        (!start || !end || (days && days.first <= end && start <= days.last)) &&
        (query.get('upcoming') !== 'true' ||
          !eventInHistory(
            { ...item, state: item.state as 'active' | 'cancelled', source_availability: 'available' },
            Date.now(),
            'Europe/Moscow',
          ));
      return HttpResponse.json({ events: visible ? [item] : [] });
    }),
    http.get(`${baseUrl}/group-events/:id`, ({ params }) =>
      params.id === item.id && item.state !== 'draft'
        ? HttpResponse.json(item)
        : HttpResponse.json({}, { status: 404 }),
    ),
  ];
}
