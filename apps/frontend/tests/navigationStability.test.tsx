import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import { renderApp, useScenario } from './helpers';

test('new routes start at the top but query-only day switches preserve scrolling', async () => {
  useScenario();
  renderApp();
  await screen.findByRole('heading', { name: 'Сегодня', level: 1 });
  const scroll = vi.mocked(window.scrollTo);
  scroll.mockClear();
  const strip = await screen.findByRole('group', { name: 'День' });
  await userEvent.click(within(strip).getAllByRole('button')[1]!);
  expect(within(strip).getAllByRole('button')[1]).toHaveAttribute('aria-pressed', 'true');
  expect(scroll).not.toHaveBeenCalled();
  await userEvent.click(within(screen.getByRole('navigation', { name: 'Основная навигация' })).getByRole('link', { name: 'Задания' }));
  expect(await screen.findByRole('heading', { name: 'Задания', level: 1 })).toBeInTheDocument();
  expect(scroll).toHaveBeenLastCalledWith({ top: 0, left: 0, behavior: 'instant' });
});

test('navigation keeps the icons mounted', async () => {
  useScenario();
  renderApp();
  await screen.findByRole('heading', { name: 'Сегодня', level: 1 });
  const nav = screen.getByRole('navigation', { name: 'Основная навигация' });
  const images = Array.from(nav.querySelectorAll('svg'));
  expect(images).toHaveLength(4);
  await userEvent.click(within(nav).getByRole('link', { name: 'Предметы' }));
  await screen.findByRole('heading', { name: 'Предметы', level: 1 });
  expect(screen.getByRole('navigation', { name: 'Основная навигация' })).toBe(nav);
  const nextImages = Array.from(nav.querySelectorAll('svg'));
  nextImages.forEach((image, index) => expect(image).toBe(images[index]));
});
