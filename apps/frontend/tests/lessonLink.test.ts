import { describe, expect, it } from 'vitest';
import { safeLessonUrl, safeRanepaCourseUrl } from '../src/lib/lessonLink';

describe('RANEPA course fallback', () => {
  it('never turns course navigation into a Join URL', () => {
    const course = 'https://lms.ranepa.ru/course/view.php?id=89282';
    const meeting = 'https://lms.ranepa.ru/mod/url/view.php?id=9911';
    expect(safeLessonUrl(course)).toBeNull();
    expect(safeLessonUrl(meeting)).toBe(meeting);
    expect(safeLessonUrl('https://zoom.us/j/1234567')).toBe('https://zoom.us/j/1234567');
    expect(safeLessonUrl('http://lms.ranepa.ru/mod/url/view.php?id=9911')).toBeNull();
    expect(safeRanepaCourseUrl(course)).toBe(course);
  });

  it('accepts only a canonical Moodle course link', () => {
    expect(safeRanepaCourseUrl('https://lms.ranepa.ru/course/view.php?id=89282'))
      .toBe('https://lms.ranepa.ru/course/view.php?id=89282');
    expect(safeRanepaCourseUrl('https://lms.ranepa.ru/course/view.php?id=89282&sesskey=x'))
      .toBeNull();
    expect(safeRanepaCourseUrl('https://evil.example/course/view.php?id=89282'))
      .toBeNull();
  });
});
