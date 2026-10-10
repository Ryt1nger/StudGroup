import clsx from 'clsx';
import { useTelegram } from '../telegram/TelegramProvider';
import button from './Button.module.css';

export function JoinLessonLink({ url, className }: { url?: string | null; className?: string }) {
  const { adapter } = useTelegram();
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null;
  } catch {
    return null;
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={clsx(button.button, button.secondary, className)}
      style={{ textDecoration: 'none' }}
      onClick={(event) => {
        if (adapter) {
          try {
            adapter.openExternalLink(url);
            event.preventDefault();
          } catch {
            /* The normal hyperlink is the fallback for older Telegram clients. */
          }
        }
      }}
    >
      Подключиться к занятию
    </a>
  );
}
