import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { buildSchedule } from '../src/mocks/data';
import { API, renderApp, server, useScenario } from './helpers';

function mockLesson(link: string | null, ended: boolean) {
  server.use(
    http.get(`${API}/schedule`, ({ request }) => {
      const query = new URL(request.url).searchParams;
      const base = buildSchedule(query.get('start')!, query.get('end')!, Date.now());
      const fromNow = ended ? -4 * 60 * 60_000 : 60 * 60_000;
      const starts = new Date(Date.now() + fromNow);
      const ends = new Date(starts.getTime() + 90 * 60_000);
      return HttpResponse.json({
        ...base,
        lessons: [{
          ...base.lessons[0],
          id: 'sdo-lesson',
          subject: 'История России',
          location: 'СДО',
          starts_at: starts.toISOString(),
          ends_at: ends.toISOString(),
          online_url: link,
          course_url: 'https://lms.ranepa.ru/course/view.php?id=88849',
        }],
      });
    }),
  );
}

describe('lesson SDO actions', () => {
  it('offers a safe Moodle course button when no teacher meeting is verified', async () => {
    useScenario();
    mockLesson(null, false);
    renderApp({ route: '/schedule/lesson/sdo-lesson' });
    const links = await screen.findAllByRole('link', { name: /Открыть курс СДО/ });
    expect(links.length).toBeGreaterThan(0);
    expect(links.some((link) => link.classList.contains('lesson-join-main'))).toBe(true);
    expect(screen.queryByRole('link', { name: 'Подключиться к паре' })).not.toBeInTheDocument();
  });

  it('retains a verified permanent meeting link after the lesson ends', async () => {
    useScenario();
    mockLesson('https://my.mts-link.ru/j/Ranepa/12345', true);
    renderApp({ route: '/schedule/lesson/sdo-lesson' });
    const link = await screen.findByRole('link', { name: 'Открыть ссылку на занятие' });
    expect(link).toHaveAttribute('href', 'https://my.mts-link.ru/j/Ranepa/12345');
  });
});
