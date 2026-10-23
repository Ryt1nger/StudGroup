import clsx from 'clsx';
import { useId, useState, type CSSProperties, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import type { NotificationSettings, UpdateProfileRequest, UserProfile } from '@studgroup/shared-types';
import { useProfileQuery, useUpdateProfile } from '../../api/queries';
import { isApiRequestError } from '../../api/errors';
import { useSession } from '../../session/SessionProvider';
import { Float } from '../../shell/AppRoot';
import { useDisplay } from '../../shell/display';
import { BackTop, initialsOf } from '../../shell/TopBar';
import { ACCENTS, useTelegram, type Accent } from '../../telegram/TelegramProvider';
import { Banner, Empty, Skeleton, Spinner } from '../../ui/Blocks';
import { useFeedback } from '../../ui/Feedback';
import { Ic } from '../../ui/Ic';
import type { IconName } from '../../ui/iconData';
import { RequestError } from '../../ui/RequestError';

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

function Icon({ name }: { name: IconName }) {
  return <span className="ri"><Ic name={name} size={18} /></span>;
}

function Toggle({ icon, title, sub, checked, onChange, dim = false }: { icon: IconName; title: string; sub?: string; checked: boolean; onChange: (value: boolean) => void; dim?: boolean }) {
  return (
    <div className={clsx('srow', dim && 'dim')}>
      <Icon name={icon} />
      <div><div className="st">{title}</div>{sub ? <div className="ss">{sub}</div> : null}</div>
      <button type="button" className="sw" role="switch" aria-checked={checked} aria-label={title} onClick={() => onChange(!checked)} />
    </div>
  );
}

function Segment<T extends string | number | null>({ title, options, value, onChange, dim = false }: { title: string; options: ReadonlyArray<readonly [T, string]>; value: T; onChange: (value: T) => void; dim?: boolean }) {
  return (
    <div className={clsx('srow stack', dim && 'dim')}>
      <div className="st">{title}</div>
      <div className="segc" role="group" aria-label={title}>
        {options.map(([v, label]) => (
          <button key={String(v)} type="button" aria-pressed={value === v} onClick={() => onChange(v)}>{label}</button>
        ))}
      </div>
    </div>
  );
}

function Group({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <>
      {title ? <div className="grph">{title}</div> : null}
      <div className="grp">{children}</div>
    </>
  );
}

function Personal() {
  const { preference, setTheme, accent, setAccent } = useTelegram();
  const [display, setDisplay] = useDisplay();
  return (
    <>
      <BackTop fallback="/profile" />
      <h1 className="ptitle">Персонализация</h1>
      <Group title="Тема">
        <Segment title="Оформление" options={THEMES} value={preference} onChange={setTheme} />
      </Group>
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
      <Group title="Вид">
        <Segment title="Размер текста" options={[['s', 'Мелкий'], ['m', 'Обычный'], ['l', 'Крупный']] as const} value={display.text} onChange={(text) => setDisplay({ text })} />
        <Segment title="Плотность списков" options={[['normal', 'Обычная'], ['compact', 'Компактная']] as const} value={display.density} onChange={(density) => setDisplay({ density })} />
        <Toggle icon="palette" title="Анимации" sub="Плавные переходы и появление карточек" checked={display.motion} onChange={(motion) => setDisplay({ motion })} />
      </Group>
    </>
  );
}

const LEADS = [['3h', 'за 3 часа'], ['1d', 'за день'], ['2d', 'за 2 дня']] as const;
const LESSON_LEADS = [[10, '10'], [15, '15'], [30, '30']] as const;
const QUIET = [['23-08', '23–08'], ['22-09', '22–09'], ['00-07', '00–07']] as const;

function Notify({ profile }: { profile: UserProfile }) {
  const save = useUpdateProfile();
  const settings = profile.settings;
  const off = !settings.enabled;
  const set = (patch: Partial<NotificationSettings>) => save.mutate({ settings: { ...settings, ...patch } });
  return (
    <>
      <BackTop fallback="/profile" />
      <h1 className="ptitle">Уведомления</h1>
      {save.isError ? <Banner tone="danger" icon="alert">Не удалось сохранить настройку. Попробуйте ещё раз.</Banner> : null}
      <Group>
        <Toggle icon="bell" title="Уведомления от бота" sub="Приходят в личные сообщения Telegram" checked={settings.enabled ?? true} onChange={(enabled) => set({ enabled })} />
      </Group>
      <Group title="Задания">
        <Toggle icon="tasks" title="Новые задания" sub="Когда в группе появилась карточка" checked={settings.new_cards ?? true} dim={off} onChange={(new_cards) => set({ new_cards })} />
        <Toggle icon="tasks" title="Изменения в заданиях" sub="Срок или условие поменялись" checked={settings.changes ?? true} dim={off} onChange={(changes) => set({ changes })} />
        <Toggle icon="clock" title="Напоминание о сроке" sub="Один раз до дедлайна" checked={settings.deadlines ?? true} dim={off} onChange={(deadlines) => set({ deadlines })} />
        <Segment title="За сколько напоминать" options={LEADS} value={settings.deadline_lead ?? '1d'} dim={off || !settings.deadlines} onChange={(deadline_lead) => set({ deadline_lead })} />
      </Group>
      <Group title="Пары">
        <Toggle icon="cal" title="Скоро начало пары" sub="Со ссылкой, если пара онлайн" checked={settings.lessons ?? false} dim={off} onChange={(lessons) => set({ lessons })} />
        <Segment title="За сколько минут" options={LESSON_LEADS} value={settings.lesson_lead_minutes ?? 15} dim={off || !settings.lessons} onChange={(lesson_lead_minutes) => set({ lesson_lead_minutes })} />
      </Group>
      <Group title="Тишина">
        <Toggle icon="moon" title="Тихие часы" sub="Без звука в это время" checked={settings.quiet ?? true} dim={off} onChange={(quiet) => set({ quiet })} />
        <Segment title="Период" options={QUIET} value={settings.quiet_range ?? '23-08'} dim={off || !settings.quiet} onChange={(quiet_range) => set({ quiet_range })} />
      </Group>
      <p className="foot">Настройки хранятся на сервере и действуют, когда бот пишет вам в личные сообщения.</p>
    </>
  );
}

const httpsOrEmpty = (value: string) => {
  if (!value) return true;
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
};

function Field({ id, label, error, children }: { id: string; label: ReactNode; error?: string | null; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {error ? <p className="ferr" role="alert">{error}</p> : null}
    </div>
  );
}

function Me({ profile }: { profile: UserProfile }) {
  const navigate = useNavigate();
  const { state } = useSession();
  // Until the person fills the form in, their Telegram name is a sensible starting point.
  const telegramName = state.status === 'ready' ? state.session.user.display_name.trim().split(/\s+/) : [];
  const { toast } = useFeedback();
  const save = useUpdateProfile();
  const ids = { first: useId(), last: useId(), email: useId(), platform: useId(), url: useId(), login: useId(), pass: useId(), cal: useId(), list: useId() };
  const [form, setForm] = useState({
    first: profile.first_name ?? telegramName[0] ?? '',
    last: profile.last_name ?? telegramName.slice(1).join(' '),
    email: profile.email ?? '',
    platform: profile.sdu.platform ?? '',
    url: profile.sdu.url ?? '',
    login: profile.sdu.login ?? '',
    cal: profile.sdu.calendar_url ?? '',
    pass: '',
  });
  const [showPass, setShowPass] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: event.target.value }));

  const failed = save.isError
    ? isApiRequestError(save.error) && save.error.status === 503
      ? 'Пароль сейчас нельзя сохранить: на сервере не настроено хранение. Остальные данные можно сохранить без него.'
      : 'Не удалось сохранить. Проверьте поля и попробуйте ещё раз.'
    : null;

  function submit() {
    const next: Record<string, string> = {};
    if (!form.first.trim()) next.first = 'Укажите имя';
    if (form.email.trim() && !/^\S+@\S+\.\S+$/.test(form.email.trim())) next.email = 'Проверьте адрес почты';
    if (!httpsOrEmpty(form.url.trim())) next.url = 'Нужна ссылка, которая начинается с https://';
    if (!httpsOrEmpty(form.cal.trim())) next.cal = 'Нужна ссылка, которая начинается с https://';
    setErrors(next);
    if (Object.keys(next).length) return;
    const body: UpdateProfileRequest = {
      first_name: form.first.trim(),
      last_name: form.last.trim() || null,
      email: form.email.trim() || null,
      sdu_platform: form.platform.trim() || null,
      sdu_url: form.url.trim() || null,
      sdu_login: form.login.trim() || null,
      sdu_calendar_url: form.cal.trim() || null,
      ...(form.pass ? { sdu_password: form.pass } : {}),
    };
    save.mutate(body, {
      onSuccess: () => {
        toast('Данные сохранены');
        void navigate('/profile');
      },
    });
  }

  return (
    <>
      <BackTop fallback="/profile" />
      <h1 className="ptitle">Мои данные</h1>
      <div className="grph">О вас</div>
      <div className="block" style={{ marginTop: 0, paddingTop: 4 }}>
        <Field id={ids.first} label="Имя" error={errors.first}><input id={ids.first} type="text" maxLength={60} autoComplete="given-name" value={form.first} onChange={set('first')} /></Field>
        <Field id={ids.last} label="Фамилия"><input id={ids.last} type="text" maxLength={60} autoComplete="family-name" value={form.last} onChange={set('last')} /></Field>
        <Field id={ids.email} label="Почта" error={errors.email}><input id={ids.email} type="email" maxLength={120} autoComplete="email" placeholder="name@example.com" value={form.email} onChange={set('email')} /></Field>
      </div>
      <Group>
        <div className="srow plain"><div className="st">Telegram</div><span className="sv">{profile.username ? `@${profile.username}` : profile.telegram_user_id}</span></div>
      </Group>
      <div className="grph">Данные СДО</div>
      <div className="block" style={{ marginTop: 0, paddingTop: 4 }}>
        <Field id={ids.platform} label="Платформа">
          <input id={ids.platform} type="text" maxLength={60} list={ids.list} placeholder="Например, Moodle" value={form.platform} onChange={set('platform')} />
          <datalist id={ids.list}><option value="Moodle" /><option value="Canvas" /><option value="Google Classroom" /><option value="Яндекс Учебник" /><option value="Другая" /></datalist>
        </Field>
        <Field id={ids.url} label="Ссылка на СДО" error={errors.url}><input id={ids.url} type="url" maxLength={200} inputMode="url" placeholder="https://" value={form.url} onChange={set('url')} /></Field>
        <Field id={ids.login} label="Логин в СДО"><input id={ids.login} type="text" maxLength={80} autoComplete="off" value={form.login} onChange={set('login')} /></Field>
        <Field id={ids.pass} label="Пароль в СДО">
          <div className="passw">
            <input id={ids.pass} type={showPass ? 'text' : 'password'} maxLength={200} autoComplete="new-password" placeholder={profile.sdu.password_set ? 'Пароль указан. Введите новый, чтобы заменить' : 'Пароль'} value={form.pass} onChange={set('pass')} />
            <button type="button" className="eye" aria-pressed={showPass} aria-label={showPass ? 'Скрыть пароль' : 'Показать пароль'} onClick={() => setShowPass((v) => !v)}><Ic name="eye" size={18} /></button>
          </div>
          {profile.sdu.password_set ? (
            <button type="button" className="linkbtn" onClick={() => save.mutate({ clear_sdu_password: true }, { onSuccess: () => toast('Пароль удалён') })}>Удалить сохранённый пароль</button>
          ) : null}
        </Field>
        <Field id={ids.cal} label={<>Ссылка на календарь СДО <span style={{ fontWeight: 500, color: 'var(--label-3)' }}>(по желанию)</span></>} error={errors.cal}><input id={ids.cal} type="url" maxLength={300} inputMode="url" placeholder="https://" value={form.cal} onChange={set('cal')} /></Field>
      </div>
      <p className="foot">Данные доступа к СДО нужны, чтобы подключаться к парам и загружать материалы. Пароль хранится зашифрованным, его видите только вы и назад он не показывается.</p>
      {failed ? <Banner tone="danger" icon="alert">{failed}</Banner> : null}
      <div className="cta-space" />
      <Float>
        <div className="cta-bar">
          <button type="button" className={clsx('btn', save.isPending && 'busy')} disabled={save.isPending} onClick={submit}>
            {save.isPending ? <><Spinner />Сохраняем…</> : 'Сохранить'}
          </button>
        </div>
      </Float>
    </>
  );
}

function ResetRow() {
  const { setTheme, setAccent } = useTelegram();
  const [, setDisplay] = useDisplay();
  const { toast } = useFeedback();
  const [armed, setArmed] = useState(false);
  return (
    <button
      type="button"
      className="srow plain"
      onClick={() => {
        if (!armed) return setArmed(true);
        setTheme(null);
        setAccent('violet');
        setDisplay({ text: 'm', density: 'normal', motion: true });
        setArmed(false);
        toast('Оформление сброшено');
      }}
    >
      <div>
        <div className="st">{armed ? 'Нажмите ещё раз' : 'Сбросить оформление'}</div>
        <div className="ss">Тема, акцент и настройки вида по умолчанию</div>
      </div>
    </button>
  );
}

function Kv({ title, value }: { title: string; value: string }) {
  return (
    <div className="srow plain">
      <div className="st">{title}</div>
      <span className="sv">{value}</span>
    </div>
  );
}

function MyGroup() {
  const { state } = useSession();
  if (state.status !== 'ready') return null;
  const { group, membership } = state.session;
  return (
    <>
      <BackTop fallback="/profile" />
      <h1 className="ptitle">Моя группа</h1>
      <div className="pcard">
        <div className="gic">{initialsOf(group?.name ?? 'SG')}</div>
        <div><b>{group?.name ?? 'Группа'}</b><span>{membership ? ROLE[membership.role] : ROLE.student}</span></div>
      </div>
      <Group title="О группе">
        <Kv title="Ваша роль" value={membership ? ROLE[membership.role] : ROLE.student} />
        <Kv title="Часовой пояс" value={group?.timezone ?? '—'} />
      </Group>
      <p className="foot">Вступить в группу или выйти из неё можно через бота StudGroup.</p>
    </>
  );
}

function ScheduleInfo() {
  const { state } = useSession();
  if (state.status !== 'ready') return null;
  const timezone = state.session.group?.timezone ?? '—';
  return (
    <>
      <BackTop fallback="/profile" />
      <h1 className="ptitle">Расписание</h1>
      <Group title="Часовой пояс">
        <Kv title="Группа" value={timezone} />
      </Group>
      <p className="foot">Часовой пояс задаёт староста группы. Все сроки и пары показываются в нём.</p>
    </>
  );
}

function Privacy() {
  return (
    <>
      <BackTop fallback="/profile" />
      <h1 className="ptitle">Данные и приватность</h1>
      <div className="block">
        <h3>Что хранится</h3>
        <p>Из сообщений группы приложение делает карточки заданий. Исходные сообщения хранятся ограниченное время, потом остаётся только карточка. Ваши отметки «Выполнено мной» видны только вам.</p>
      </div>
      <Group title="На этом устройстве">
        <ResetRow />
      </Group>
    </>
  );
}

function Help() {
  return (
    <>
      <BackTop fallback="/profile" />
      <h1 className="ptitle">Помощь</h1>
      <Group title="Приложение">
        <Kv title="Язык" value="Русский" />
      </Group>
      <p className="foot">StudGroup · задания и расписание вашей группы в Telegram</p>
    </>
  );
}

function Home() {
  const { state } = useSession();
  const { preference, accent } = useTelegram();
  const profileQuery = useProfileQuery();
  if (state.status !== 'ready') return null;
  const { user, group, membership } = state.session;
  const profile = profileQuery.data;
  const name = [profile?.first_name, profile?.last_name].filter(Boolean).join(' ') || user.display_name;
  const role = membership ? ROLE[membership.role] : ROLE.student;
  const theme = preference === 'light' ? 'Светлая' : preference === 'dark' ? 'Тёмная' : 'Системная';
  const enabled = profile?.settings.enabled ?? true;
  return (
    <>
      <BackTop fallback="/today" />
      <Link className="pcard big" to="/profile/me" aria-label="Мои данные">
        <div className="avatar big">{initialsOf(name)}</div>
        <div className="pc-text">
          <b>{name}</b>
          <span>{[role, group?.name].filter(Boolean).join(' · ')}</span>
          <em><Ic name="check" size={12} />{profile?.sdu.platform ? `СДО: ${profile.sdu.platform}` : 'Вход через Telegram'}</em>
        </div>
        <span className="pc-chev"><Ic name="chev" /></span>
      </Link>
      <Group title="Учёба">
        <Link className="srow" to="/profile/group">
          <Icon name="users" />
          <div><div className="st">Моя группа</div></div>
          <span className="sv">{group?.name}<Ic name="chev" /></span>
        </Link>
        <Link className="srow" to="/profile/schedule">
          <Icon name="cal" />
          <div><div className="st">Расписание</div></div>
          <span className="sv"><Ic name="chev" /></span>
        </Link>
      </Group>
      <Group title="Приложение">
        <Link className="srow" to="/profile/personal">
          <Icon name="palette" />
          <div><div className="st">Персонализация</div></div>
          <span className="sv">{theme}, {ACCENT_INFO[accent].name.toLowerCase()}<Ic name="chev" /></span>
        </Link>
        <Link className="srow" to="/profile/notify">
          <Icon name="bell" />
          <div><div className="st">Уведомления</div></div>
          <span className="sv">{enabled ? 'Включены' : 'Выключены'}<Ic name="chev" /></span>
        </Link>
      </Group>
      <Group title="Данные и помощь">
        <Link className="srow" to="/profile/privacy">
          <Icon name="shield" />
          <div><div className="st">Данные и приватность</div></div>
          <span className="sv"><Ic name="chev" /></span>
        </Link>
        <Link className="srow" to="/profile/about">
          <Icon name="info" />
          <div><div className="st">Помощь</div></div>
          <span className="sv"><Ic name="chev" /></span>
        </Link>
      </Group>
      {membership && membership.role !== 'student' ? <p className="foot">Староста и помощник могут менять карточки заданий и событий.</p> : null}
    </>
  );
}

/** Screens that need the loaded profile show its loading and error states. */
function WithProfile({ children }: { children: (profile: UserProfile) => ReactNode }) {
  const query = useProfileQuery();
  if (query.data) return <>{children(query.data)}</>;
  return (
    <>
      <BackTop fallback="/profile" />
      {query.isPending ? <Skeleton label="Загрузка" /> : null}
      {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
    </>
  );
}

export function ProfileScreen() {
  const { section } = useParams();
  if (section === undefined) return <Home />;
  if (section === 'personal') return <Personal />;
  if (section === 'group') return <MyGroup />;
  if (section === 'schedule') return <ScheduleInfo />;
  if (section === 'privacy') return <Privacy />;
  if (section === 'about') return <Help />;
  if (section === 'me') return <WithProfile>{(profile) => <Me profile={profile} />}</WithProfile>;
  if (section === 'notify') return <WithProfile>{(profile) => <Notify profile={profile} />}</WithProfile>;
  return (
    <>
      <BackTop fallback="/profile" />
      <Empty title="Раздел не найден" body="Откройте профиль и выберите нужный раздел." />
    </>
  );
}
