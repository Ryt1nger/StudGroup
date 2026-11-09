import { describe, expect, it } from 'vitest';
import { safeRanepaCourseUrl } from '../src/lib/lessonLink';

describe('RANEPA course fallback', () => {
  it('accepts only a canonical Moodle course link', () => {
    expect(safeRanepaCourseUrl('https://lms.ranepa.ru/course/view.php?id=89282'))
      .toBe('https://lms.ranepa.ru/course/view.php?id=89282');
    expect(safeRanepaCourseUrl('https://lms.ranepa.ru/course/view.php?id=89282&sesskey=x'))
      .toBeNull();
    expect(safeRanepaCourseUrl('https://evil.example/course/view.php?id=89282'))
      .toBeNull();
  });
});
