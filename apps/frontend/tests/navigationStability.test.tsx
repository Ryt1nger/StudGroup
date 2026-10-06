import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { renderApp, useScenario } from './helpers';

test('navigation keeps both icon images mounted with unchanged sources', async () => {
  useScenario();
  renderApp();
  await screen.findByRole('heading', { name: 'Сегодня', level: 1 });
  const nav = screen.getByRole('navigation', { name: 'Основная навигация' });
  const images = Array.from(nav.querySelectorAll('img'));
  expect(images).toHaveLength(8);
  const sources = images.map((image) => image.getAttribute('src'));
  await userEvent.click(within(nav).getByRole('link', { name: 'Предметы' }));
  await screen.findByRole('heading', { name: 'Предметы', level: 1 });
  expect(screen.getByRole('navigation', { name: 'Основная навигация' })).toBe(nav);
  const nextImages = Array.from(nav.querySelectorAll('img'));
  nextImages.forEach((image, index) => expect(image).toBe(images[index]));
  expect(nextImages.map((image) => image.getAttribute('src'))).toEqual(sources);
});
