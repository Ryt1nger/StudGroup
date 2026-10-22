import clsx from 'clsx';
import button from './Button.module.css';
import { safeLessonUrl, useLessonLinkClick } from './lessonLink';

export function JoinLessonLink({ url, className }: { url?: string | null; className?: string }) {
  const safe = safeLessonUrl(url);
  const onClick = useLessonLinkClick(safe ?? '');
  if (!safe) return null;
  return (
    <a
      href={safe}
      target="_blank"
      rel="noopener noreferrer"
      className={clsx(button.button, button.secondary, className)}
      style={{ textDecoration: 'none' }}
      onClick={onClick}
    >
      Подключиться к занятию
    </a>
  );
}
