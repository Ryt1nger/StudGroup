/** Development-only timetable confirmation flow. Persistence/permissions are tested on backend. */
import type { ConfirmImageScheduleSuggestionData, ScheduleSuggestion } from '@studgroup/shared-types';
import { http, HttpResponse } from 'msw';
import { addDays, dayKey } from '../lib/format';
import { buildSchedule } from './data';

export function imageScheduleHandlers(baseUrl: string) {
  let first = addDays(dayKey(Date.now(), 'Europe/Moscow'), 1);
  while ([0, 6].includes(new Date(`${first}T12:00:00Z`).getUTCDay())) first = addDays(first, 1);
  const weekday = (new Date(`${first}T12:00:00Z`).getUTCDay() + 6) % 7;
  const id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
  let version = 0;
  let confirmation: ConfirmImageScheduleSuggestionData['body'] | null = null;
  const rows: ScheduleSuggestion['rows'] = [
    {
      weekday,
      starts: '13:00',
      ends: '14:30',
      subject: 'Математический анализ',
      teacher: 'Иванов',
      room: '201',
      week: 'all',
      valid_from: first,
      valid_until: first,
    },
  ];
  function schedule(start: string, end: string) {
    const data = buildSchedule(start, end, Date.now());
    const corrected = confirmation?.corrected_rows?.[0];
    const target = confirmation?.targets?.[0];
    if (corrected && target)
      data.lessons = data.lessons.map((lesson) =>
        lesson.id !== target.lesson_id
          ? lesson
          : {
              ...lesson,
              subject: corrected.subject,
              starts_at: `${first}T${corrected.starts.slice(0, 5)}:00+03:00`,
              ends_at: `${first}T${corrected.ends.slice(0, 5)}:00+03:00`,
              location: corrected.room ?? null,
              teacher: corrected.teacher ?? null,
              subgroup_id: corrected.subgroup_id,
            },
      );
    return data;
  }
  return [
    http.get(`${baseUrl}/staff/image-findings/schedule`, () =>
      HttpResponse.json({
        current_schedule_version: version,
        subgroups: [],
        suggestions: [
          {
            id,
            revision: 1,
            state: confirmation ? 'confirmed' : 'pending',
            rows,
            schedule_version: 0,
            confirmation: null,
            source_url: 'https://t.me/c/123456/41',
          },
        ],
      }),
    ),
    http.get(`${baseUrl}/staff/schedule`, ({ request }) => {
      const query = new URL(request.url).searchParams;
      const data = schedule(query.get('start')!, query.get('end')!);
      return HttpResponse.json({
        schedule: data,
        schedule_version: version,
        no_lesson_days: [],
        lesson_edits: data.lessons.map((lesson) => ({
          lesson_id: lesson.id,
          pattern_revision: 1,
          override_revision: 0,
          week: 'all',
          single_day: false,
        })),
      });
    }),
    http.post(`${baseUrl}/staff/image-findings/schedule/${id}/confirm`, async ({ request }) => {
      const body = (await request.json()) as ConfirmImageScheduleSuggestionData['body'];
      if (!confirmation && body.expected_schedule_version !== version)
        return HttpResponse.json({}, { status: 409 });
      if (!confirmation) {
        confirmation = body;
        version += 1;
      }
      return HttpResponse.json({ schedule_version: version });
    }),
    http.get(`${baseUrl}/schedule`, ({ request }) => {
      const query = new URL(request.url).searchParams;
      return HttpResponse.json(schedule(query.get('start')!, query.get('end')!));
    }),
  ];
}
