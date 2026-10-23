import type { CSSProperties } from 'react';
import { Link, useParams } from 'react-router';
import { useSession } from '../../session/SessionProvider';
import { initialsOf } from '../../shell/TopBar';
import { BackTop } from '../../shell/TopBar';
import { ACCENTS, useTelegram, type Accent } from '../../telegram/TelegramProvider';
import { Empty } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';

const ROLE = { student: 'Студент', headman: 'Староста', deputy: 'Помощник старосты' } as const;

const ACCENT_INFO: Record<Accent, { name: string; h: number; s: string; l: string; on?: string }> = {
  green: { name: 'Зелёный', h: 150, s: '60%', l: '42%' },
  red: { name: 'Красный', h: 355, s: '86%', l: '60%' },
  violet: { name: 'Фиолетовый', h: 246, s: '100%', l: '68%' },
  blue: { name: 'Голубой', h: 212, s: '100%', l: '58%' },
  gray: { name: 'Серый', h: 230, s: '8%', l: '56%' },
  yellow: { name: 'Жёлтый', h: 44, s: '100%', l: '52%', on: '#1c1500' },
};

const THEMES = [
  ['light', 'Светлая'],
  ['dark', 'Тёмная'],
  [null, 'Системная'],
] as const;

function Personal() {
  const { preference, setTheme, accent, setAccent } = useTelegram();
  return (
    <>
      <BackTop fallback="/profile" />
      <h1 className="ptitle">Персонализация</h1>
      <div className="grph">Тема</div>
      <div className="grp">
        <div className="srow stack">
          <div className="st">Оформление</div>
          <div className="segc" role="group" aria-label="Оформление">
            {THEMES.map(([value, label]) => (
              <button key={label} type="button" aria-pressed={preference === value} onClick={() => setTheme(value)}>{label}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="grph">Цвет акцента</div>
      <div className="block" style={{ marginTop: 0 }}>
        <div className="swatches" role="group" aria-label="Цвет акцента">
          {ACCENTS.map((id) => {
            const info = ACCENT_INFO[id];
            return (
              <button
                key={id}
                type="button"
                className="swb"
                aria-pressed={accent === id}
                style={{ ['--h' as string]: info.h, ['--s' as string]: info.s, ['--l' as string]: info.l, ['--oc' as string]: info.on ?? '#fff' } as CSSProperties}
                onClick={() => setAccent(id)}
              >
                <i />
                <span>{info.name}</span>
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}

function Home() {
  const { state } = useSession();
  const { preference, accent } = useTelegram();
  if (state.status !== 'ready') return null;
  const { user, group, membership } = state.session;
  const role = membership ? ROLE[membership.role] : ROLE.student;
  const theme = preference === 'light' ? 'Светлая' : preference === 'dark' ? 'Тёмная' : 'Системная';
  return (
    <>
      <BackTop fallback="/today" />
      <div className="pcard big">
        <div className="avatar big">{initialsOf(user.display_name)}</div>
        <div className="pc-text">
          <b>{user.display_name}</b>
          <span>{[role, group?.name].filter(Boolean).join(' · ')}</span>
          <em><Ic name="check" size={12} />Вход через Telegram</em>
        </div>
      </div>
      <div className="grph">Приложение</div>
      <div className="grp">
        <Link className="srow" to="/profile/personal">
          <span className="ri"><Ic name="palette" size={18} /></span>
          <div><div className="st">Персонализация</div></div>
          <span className="sv">{theme}, {ACCENT_INFO[accent].name.toLowerCase()}<Ic name="chev" /></span>
        </Link>
      </div>
      {membership?.role !== 'student' ? <p className="foot">Староста и помощник могут менять карточки заданий и событий.</p> : null}
    </>
  );
}

export function ProfileScreen() {
  const { section } = useParams();
  if (section === undefined) return <Home />;
  if (section === 'personal') return <Personal />;
  return (
    <>
      <BackTop fallback="/profile" />
      <Empty title="Раздел не найден" body="Откройте профиль и выберите нужный раздел." />
    </>
  );
}
