import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { renderApp, useScenario } from './helpers';

test('day switches stay inside Today and sections change in place', async () => {
  useScenario();
  renderApp();
  await screen.findByRole('heading', { name: 'Сегодня', level: 1 });
  const strip = await screen.findByRole('group', { name: 'День' });
  await userEvent.click(within(strip).getAllByRole('button')[1]!);
  expect(within(strip).getAllByRole('button')[1]).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(within(screen.getByRole('navigation', { name: 'Основная навигация' })).getByRole('link', { name: 'Задания' }));
  expect(await screen.findByRole('heading', { name: 'Задания', level: 1 })).toBeInTheDocument();
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
