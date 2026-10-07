import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MaterialLinks } from '../src/ui/MaterialLinks';

describe('real material links', () => {
  it('renders HTTPS materials with protected external navigation', () => {
    render(<MaterialLinks items={[{ id: 'm1', title: 'Учебник', url: 'https://example.com/book' }]} />);
    expect(screen.getByRole('link', { name: 'Учебник' })).toHaveAttribute('href', 'https://example.com/book');
    expect(screen.getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer');
  });
  it('never renders unsafe links and distinguishes connected empty data', () => {
    render(<MaterialLinks items={[{ id: 'm1', title: 'Unsafe', url: 'javascript:alert(1)' }]} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('Материалы пока не добавлены.')).toBeInTheDocument();
  });
});
