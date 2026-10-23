import type { MouseEvent } from 'react';
import { useTelegram } from '../telegram/TelegramProvider';

/** The joining URL when it is safe to open (https, no embedded credentials), otherwise null. */
export function safeLessonUrl(url?: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null;
  } catch {
    return null;
  }
  return url;
}

/** Click handler that opens the lesson through Telegram, keeping the plain link as the fallback. */
export function useLessonLinkClick(url: string) {
  const { adapter } = useTelegram();
  return (event: MouseEvent) => {
    if (!adapter) return;
    try {
      adapter.openExternalLink(url);
      event.preventDefault();
    } catch {
      /* The normal hyperlink is the fallback for older Telegram clients. */
    }
  };
}
