import { describe, expect, it } from 'vitest';
import type { LessonOccurrence } from '@studgroup/shared-types';
import { activeSubjectLessons } from './SubjectView';

describe('subject schedule', () => {
  it('shows the replacement without repeating its cancelled source lesson', () => {
    const base = {
      subject: 'Иностранный язык',
      starts_at: '2026-11-07T10:40:00+03:00',
      ends_at: '2026-11-07T12:00:00+03:00',
    };
    const lessons = [
      { ...base, id: 'regular:2026-11-07', status: 'cancelled' },
      { ...base, id: 'replacement:2026-11-07', status: 'scheduled' },
    ] as LessonOccurrence[];

    expect(activeSubjectLessons(lessons).map((lesson) => lesson.id)).toEqual([
      'replacement:2026-11-07',
    ]);
  });
});
