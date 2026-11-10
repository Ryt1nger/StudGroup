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

/** Copy the exact meeting URL, including in older Telegram WebViews without Clipboard API. */
export async function copyLessonUrl(url: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
      return true;
    }
  } catch {
    // Older Telegram clients may reject Clipboard API. Try the classic selection fallback.
  }

  const field = document.createElement('textarea');
  field.value = url;
  field.readOnly = true;
  field.style.position = 'fixed';
  field.style.opacity = '0';
  field.style.left = '-9999px';
  document.body.appendChild(field);
  try {
    field.focus();
    field.select();
    field.setSelectionRange(0, url.length);
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    field.remove();
  }
}

/** Course navigation is separate from a verified meeting link. */
export function safeRanepaCourseUrl(url?: string | null): string | null {
  if (!safeLessonUrl(url)) return null;
  try {
    const parsed = new URL(url!);
    if (parsed.hostname !== 'lms.ranepa.ru' || parsed.pathname !== '/course/view.php' || parsed.hash) return null;
    const keys = [...parsed.searchParams.keys()];
    if (keys.length !== 1 || keys[0] !== 'id' || !/^\d+$/.test(parsed.searchParams.get('id') ?? '')) return null;
    return parsed.href;
  } catch {
    return null;
  }
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
