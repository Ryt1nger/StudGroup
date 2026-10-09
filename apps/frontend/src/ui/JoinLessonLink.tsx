import clsx from 'clsx';
import button from './Button.module.css';

export function JoinLessonLink({ url, className }: { url?: string | null; className?: string }) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null;
  } catch { return null; }
  return <a href={url} target="_blank" rel="noopener noreferrer"
    className={clsx(button.button, button.secondary, className)}
    style={{ textDecoration: 'none' }}
    onClick={(event) => {
      const app = window.Telegram?.WebApp;
      if (app?.openLink) {
        try { app.openLink(url, { try_instant_view: false }); event.preventDefault(); }
        catch { /* The normal hyperlink is the fallback for older Telegram clients. */ }
      }
    }}>Подключиться к занятию</a>;
}
