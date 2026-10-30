import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { useProfileQuery } from '../api/queries';
import { useSession } from '../session/SessionProvider';
import { Ic } from '../ui/Ic';
import { useBack } from './useBack';
import { BellButton } from './Shade';

function greeting(date = new Date()): string {
  const hour = date.getHours();
  return hour < 5 ? 'Доброй ночи,' : hour < 12 ? 'Доброе утро,' : hour < 18 ? 'Добрый день,' : 'Добрый вечер,';
}

export function initialsOf(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map((part) => part.charAt(0)).join('').toUpperCase() || '?';
}

/** Bell and avatar: the two buttons at the right of every top row. */
/** The name the person gave in their profile, else the one Telegram knows. */
function useOwnName(): { first: string; full: string } {
  const { state } = useSession();
  const profileQuery = useProfileQuery({ enabled: state.status === 'ready' });
  const profile = profileQuery.data;
  const telegram = state.status === 'ready' ? state.session.user.display_name : '';
  // Never briefly reveal the Telegram name while a saved profile name is still loading.
  if (!profile && profileQuery.isFetching) return { first: '', full: '' };
  const full = [profile?.first_name, profile?.last_name].filter(Boolean).join(' ') || telegram;
  return { first: profile?.first_name ?? telegram.split(/\s+/)[0] ?? '', full };
}

function Actions() {
  const { state } = useSession();
  const { full: name } = useOwnName();
  if (state.status !== 'ready') return null;
  return (
    <div className="tr">
      <BellButton />
      <Link className="avatar" to="/profile" aria-label={`Профиль: ${name}`}>{initialsOf(name)}</Link>
    </div>
  );
}

/** Top row of a section: «Сегодня» greets the user, the other sections show the group name over their title. */
export function SectionTop({ title }: { title?: string }) {
  const { state } = useSession();
  const { first } = useOwnName();
  const ready = state.status === 'ready' ? state.session : null;
  const group = ready?.group?.name ?? 'StudGroup';
  return (
    <div className="top">
      <div className="hi">
        <span>{title ? group : greeting()}</span>
        <b>{(title ?? first) || '\u00a0'}</b>
      </div>
      <Actions />
    </div>
  );
}

/** Top row of «Задания»/«Дедлайны»: the group name only; the section titles sit below it. */
export function GroupTop() {
  const { state } = useSession();
  const group = state.status === 'ready' ? (state.session.group?.name ?? 'StudGroup') : 'StudGroup';
  return (
    <div className="top">
      <div className="hi"><span>{group}</span></div>
      <Actions />
    </div>
  );
}

/** Top row of a detail screen: back arrow (hidden when Telegram shows its own) and optional actions. */
export function BackTop({ fallback, right }: { fallback: string; right?: ReactNode }) {
  // Our own arrow is always shown, like in the design; the native Telegram BackButton does the same thing.
  const { go } = useBack(fallback);
  return (
    <div className="top">
      <a
        className="icon-btn"
        href={fallback}
        aria-label="Назад"
        onClick={(event) => {
          event.preventDefault();
          go();
        }}
      >
        <Ic name="back" />
      </a>
      {right ? <div className="tr">{right}</div> : null}
    </div>
  );
}
