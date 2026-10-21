import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { photoMaterials, remainingMaterials } from '../src/lib/photoMaterials';
import { CardPhotos } from '../src/ui/CardPhotos';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../src/api/queries';
import type { ReactElement } from 'react';

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const photo = {
  id: 'p1',
  title: 'Фото',
  url: 'https://t.me/c/1/2',
  kind: 'photo' as const,
  replaces_description: true,
};

describe('photo cards', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:photo');
    URL.revokeObjectURL = vi.fn();
  });

  it('keeps only photos that are the task itself', () => {
    const link = { id: 'l1', title: 'Сайт', url: 'https://example.com' };
    const attached = { ...photo, id: 'p2', replaces_description: false };
    expect(photoMaterials([link, photo, attached])).toEqual([photo]);
    expect(remainingMaterials([link, photo, attached])).toEqual([link, attached]);
    expect(photoMaterials(undefined)).toEqual([]);
  });

  it('shows the picture and opens it full screen', async () => {
    vi.spyOn(api, 'attachmentImage').mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
    renderWithClient(<CardPhotos items={[photo]} alt="Алгебра, №12" />);
    const image = await screen.findByRole('img', { name: 'Алгебра, №12' });
    expect(image).toHaveAttribute('src', 'blob:photo');
    fireEvent.click(screen.getByRole('button', { name: 'Открыть фото на весь экран' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть фото' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('offers a retry when the photo cannot be loaded', async () => {
    const load = vi.spyOn(api, 'attachmentImage').mockRejectedValue(new Error('down'));
    renderWithClient(<CardPhotos items={[photo]} alt="Фото" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Фото сейчас недоступно');
    const calls = load.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(load.mock.calls.length).toBeGreaterThan(calls));
  });
});
