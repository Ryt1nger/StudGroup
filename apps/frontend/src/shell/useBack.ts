import { useCallback } from 'react';
import { useNavigate } from 'react-router';
import { useBackButton } from './useBackButton';

/**
 * Back navigation of a detail screen: one step back in history when there is one, else the section it
 * belongs to. The native Telegram BackButton, when present, does the same.
 */
export function useBack(fallback: string): { go: () => void; native: boolean } {
  const navigate = useNavigate();
  const go = useCallback(() => {
    const index = (window.history.state as { idx?: number } | null)?.idx ?? 0;
    if (index > 0) void navigate(-1);
    else void navigate(fallback, { replace: true });
  }, [navigate, fallback]);
  const native = useBackButton(go);
  return { go, native };
}
