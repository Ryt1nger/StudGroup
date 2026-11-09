import { useState, type CSSProperties, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useProfileQuery, useUpdateProfile } from '../../api/queries';
import { acceptLegal, legalStatus, type LegalStatus } from '../../api/legal';
import {
  completeOnboarding, getOnboarding, getStudentSplits, selectStudentSplit,
  setOnboardingStep, type OnboardingState,
} from '../../api/onboarding';
import { useSession } from '../../session/SessionProvider';
import { Splash } from '../../session/AccessGate';
import { ACCENTS, useTelegram } from '../../telegram/TelegramProvider';
import { useDisplay } from '../../shell/display';
import { ACCENT_INFO, THEMES, TEXT_SIZES, DENSITIES } from '../../shell/appearanceOptions';
import welcomeLight from '../../../../../design/extracted/illustrations/glass-cards.light.png';
import welcomeDark from '../../../../../design/extracted/illustrations/glass-cards.dark.png';
import personalLight from '../../../../../design/extracted/illustrations/folder-large.light.png';
import personalDark from '../../../../../design/extracted/illustrations/folder-large.dark.png';
import contactsLight from '../../../../../design/extracted/illustrations/illustration-announcement.light.png';
import contactsDark from '../../../../../design/extracted/illustrations/illustration-announcement.dark.png';
import subgroupLight from '../../../../../design/extracted/illustrations/folder-compact.light.png';
import subgroupDark from '../../../../../design/extracted/illustrations/folder-compact.dark.png';
import sdoLight from '../../../../../design/extracted/illustrations/illustration-materials.light.png';
import sdoDark from '../../../../../design/extracted/illustrations/illustration-materials.dark.png';
import finishLight from '../../../../../design/extracted/illustrations/sparkle-badge.light.png';
import finishDark from '../../../../../design/extracted/illustrations/sparkle-badge.dark.png';
import './onboarding.css';

const steps = ['welcome', 'personal', 'contacts', 'subgroups', 'sdo', 'legal', 'appearance', 'finish'] as const;
type Step = (typeof steps)[number];
type Checks = { terms: boolean; service: boolean; sdo: boolean };
type SdoDraft = { login: string; password: string };
const ART: Record<Step, [string, string]> = {
  welcome: [welcomeLight, welcomeDark],
  personal: [personalLight, personalDark],
  contacts: [contactsLight, contactsDark],
  subgroups: [subgroupLight, subgroupDark],
  sdo: [sdoLight, sdoDark],
  legal: [finishLight, finishDark],
  appearance: [subgroupLight, subgroupDark],
  finish: [finishLight, finishDark],
};

function Shell({ children, role, group, step }: { children: ReactNode; role: string; group: string; step: Step }) {
  const index = steps.indexOf(step);
  const { scheme } = useTelegram();
  const art = ART[step][scheme === 'dark' ? 1 : 0];
  return (
    <div className="ob-root">
      <main className="ob-card">
        <header className="ob-topline">
          <div className="ob-brand">
            <img src={`${import.meta.env.BASE_URL}studgroup-logo.png`} width="42" height="42" alt="" />
            <span>StudGroup</span>
          </div>
          <span className="ob-campus">{group}</span>
        </header>
        <div className="ob-role">ПЕРВЫЙ ЗАПУСК · {role}</div>
        <div className="ob-progress-head">
          <span>Шаг {index + 1} из {steps.length}</span>
          <span>{Math.round(((index + 1) / steps.length) * 100)}%</span>
        </div>
        <div className="ob-progress" aria-label={`Шаг ${index + 1} из ${steps.length}`}>
          {steps.map((s, i) => <span key={s} className={'ob-progress-segment ' + (i <= index ? 'is-active' : '')} />)}
        </div>
        <div className={'ob-art ob-art-' + step} aria-hidden="true"><img src={art} alt="" /></div>
        <section className="ob-scene" key={step}>{children}</section>
      </main>
    </div>
  );
}

function Notice({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="ob-notice" role="alert">{message}{retry ? <button type="button" onClick={retry}>Повторить</button> : null}</div>;
}

function Actions({ back, next, busy = false, disabled = false, label = 'Далее' }: {
  back?: () => void; next: () => void; busy?: boolean; disabled?: boolean; label?: string;
}) {
  return (
    <div className="ob-actions">
      <button type="button" className="ob-button ob-primary" disabled={busy || disabled} onClick={next}>
        {busy ? 'Сохраняем…' : label} <span aria-hidden="true">→</span>
      </button>
      {back ? <button type="button" className="ob-button ob-secondary" disabled={busy} onClick={back}>Назад</button> : null}
    </div>
  );
}

function Title({ children, lead, badge }: { children: ReactNode; lead: string; badge?: string }) {
  return <><h1>{children}</h1>{badge ? <span className="ob-badge">{badge}</span> : null}<p className="ob-lead">{lead}</p></>;
}

export function OnboardingGate({ children }: { children: ReactNode }) {
  const { state } = useSession();
  const active = state.status === 'ready' && state.session.access_state === 'active';
  const isDemo = import.meta.env.MODE === 'demo';
  const membershipId = active ? state.session.membership?.id : null;
  const progress = useQuery({
    queryKey: ['first-run', membershipId], queryFn: getOnboarding,
    enabled: Boolean(membershipId) && !isDemo,
    retry: 1, staleTime: 60_000, refetchOnWindowFocus: false,
  });
  if (!active || !membershipId || isDemo) return <>{children}</>;
  // Never paint the onboarding UI until the server has verified its status.
  // Existing completed memberships see only the same neutral splash as bootstrap,
  // not a registration form that immediately disappears.
  if (progress.isPending) return <Splash />;
  if (progress.isError || !progress.data) {
    return <div className="ob-boot-error">
      <Notice message="Не удалось проверить статус настройки. Данные не сброшены." retry={() => { void progress.refetch(); }} />
    </div>;
  }
  if (progress.data.completed) return <>{children}</>;
  return <Wizard state={progress.data} membershipId={membershipId} />;
}

function useMove(membershipId: string) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function move(step: Step) {
    setBusy(true);
    setError('');
    try {
      await setOnboardingStep(step);
      queryClient.setQueryData<OnboardingState>(['first-run', membershipId], previous =>
        previous ? { ...previous, step } : previous);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить шаг');
    } finally { setBusy(false); }
  }
  async function finish() {
    setBusy(true);
    setError('');
    try {
      await completeOnboarding();
      queryClient.setQueryData<OnboardingState>(['first-run', membershipId], previous =>
        previous ? { ...previous, step: 'complete', completed: true } : previous);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось завершить настройку');
    } finally { setBusy(false); }
  }
  return { move, finish, busy, error, setError };
}

function Wizard({ state, membershipId }: { state: OnboardingState; membershipId: string }) {
  const isStaff = state.role !== 'student';
  const stage: Step = steps.includes(state.step as Step) ? state.step as Step : 'welcome';
  const progress = useMove(membershipId);
  // Staff had a legacy one-form entry stage named 'personal'. Show the intro
  // before that stage without changing their persisted backend state.
  const [staffIntro, setStaffIntro] = useState(isStaff && stage === 'personal');
  // Password is only held in this wizard's memory until legal consent is confirmed.
  // Never write it to cookies, storage or the React Query cache.
  const [sdoDraft, setSdoDraft] = useState<SdoDraft | null>(null);
  const shown: Step = staffIntro && stage === 'personal' ? 'welcome' : stage;
  const index = steps.indexOf(stage);
  const back = shown === 'welcome' ? undefined : () => {
    if (isStaff && stage === 'personal') setStaffIntro(true);
    else void progress.move(steps[index - 1]!);
  };
  const next = (step: Step) => () => { void progress.move(step); };
  return (
    <Shell step={shown} role={isStaff ? (state.role === 'headman' ? 'СТАРОСТА' : 'ЗАМЕСТИТЕЛЬ') : 'СТУДЕНТ'} group={state.group_name}>
      {shown === 'welcome' ? (
        <>
          <Title lead="Пара минут — и все задания, пары и события вашей группы будут под рукой.">Настроим StudGroup под вас</Title>
          <div className="ob-feature-list">
            <div><span className="ob-feature-icon">▦</span><span><strong>Удобное расписание</strong><small>Пары и важные изменения</small></span></div>
            <div><span className="ob-feature-icon">☑</span><span><strong>Задания и дедлайны</strong><small>Всё важное по предметам</small></span></div>
            <div><span className="ob-feature-icon">♧</span><span><strong>События и объявления</strong><small>Новости вашей группы в одном месте</small></span></div>
          </div>
          <Actions busy={progress.busy} label="Начать настройку"
            next={() => { if (isStaff) setStaffIntro(false); else void progress.move('personal'); }} />
        </>
      ) : null}
      {shown === 'personal' ? <PersonalStep back={back} busy={progress.busy} next={next('contacts')} /> : null}
      {shown === 'contacts' ? <ContactsStep back={back} busy={progress.busy} next={next('subgroups')} /> : null}
      {shown === 'subgroups' ? <SubgroupStep staff={isStaff} back={back} busy={progress.busy} next={next('sdo')} /> : null}
      {shown === 'sdo' ? <CredentialsStep back={back} busy={progress.busy} next={next('legal')}
        draft={sdoDraft} onDraft={setSdoDraft} /> : null}
      {shown === 'legal' ? <LegalStep required={state.legal_required} back={back} busy={progress.busy}
        next={next('appearance')} pendingSdo={sdoDraft} clearSdo={() => setSdoDraft(null)} /> : null}
      {shown === 'appearance' ? <AppearanceStep back={back} busy={progress.busy} next={next('finish')} /> : null}
      {shown === 'finish' ? (
        <>
          <Title lead="Ваш профиль настроен. Теперь можно пользоваться расписанием, заданиями и событиями группы.">Настройка завершена!</Title>
          <div className="ob-done-lines">
            <span><b>✓</b> Группа: {state.group_name}</span>
            <span><b>✓</b> Личные данные сохранены</span>
            <span><b>✓</b> Оформление настроено</span>
          </div>
          <Actions back={back} busy={progress.busy} label="Перейти в StudGroup" next={() => { void progress.finish(); }} />
        </>
      ) : null}
      {progress.error ? <Notice message={progress.error} /> : null}
    </Shell>
  );
}

function PersonalStep({ back, busy, next }: { back?: () => void; busy: boolean; next: () => void }) {
  const profile = useProfileQuery();
  const save = useUpdateProfile();
  const [first, setFirst] = useState<string | null>(null);
  const [last, setLast] = useState<string | null>(null);
  const [error, setError] = useState('');
  const firstValue = first ?? profile.data?.first_name ?? '';
  const lastValue = last ?? profile.data?.last_name ?? '';
  async function submit() {
    if (!firstValue.trim()) { setError('Укажите имя'); return; }
    try {
      setError('');
      await save.mutateAsync({ first_name: firstValue.trim(), last_name: lastValue.trim() || null });
      next();
    } catch { setError('Не удалось сохранить данные. Проверьте соединение.'); }
  }
  return (
    <>
      <Title lead="Укажите имя и фамилию, чтобы мы могли правильно отображать ваш профиль и подписи к карточкам.">Как к вам обращаться?</Title>
      <div className="ob-form">
        <label>Имя <span className="ob-required">*</span><input type="text" autoComplete="given-name" maxLength={60}
          placeholder="Ваше имя" value={firstValue} onChange={e => setFirst(e.target.value)} required /></label>
        <label>Фамилия <small>необязательно</small><input type="text" autoComplete="family-name" maxLength={60}
          placeholder="Ваша фамилия" value={lastValue} onChange={e => setLast(e.target.value)} /></label>
      </div>
      <div className="ob-help">ⓘ Эти данные можно изменить позже в профиле.</div>
      {error ? <Notice message={error} /> : null}
      <Actions back={back} busy={busy || save.isPending} disabled={profile.isPending || profile.isError} next={() => { void submit(); }} />
    </>
  );
}

function ContactsStep({ back, busy, next }: { back?: () => void; busy: boolean; next: () => void }) {
  const profile = useProfileQuery();
  const save = useUpdateProfile();
  const [email, setEmail] = useState<string | null>(null);
  const [error, setError] = useState('');
  const value = email ?? profile.data?.email ?? '';
  async function submit() {
    if (value.trim() && !/^\S+@\S+\.\S+$/.test(value.trim())) { setError('Проверьте адрес почты'); return; }
    try {
      setError('');
      await save.mutateAsync({ email: value.trim() || null });
      next();
    } catch { setError('Не удалось сохранить почту'); }
  }
  return (
    <>
      <Title lead="Почта пригодится для связи. Telegram уже подключён, поэтому этот шаг можно пропустить.">Контакты для связи</Title>
      <div className="ob-form"><label>Почта <small>необязательно</small><input type="email" autoComplete="email"
        placeholder="name@example.com" maxLength={120} value={value} onChange={e => setEmail(e.target.value)} /></label></div>
      <div className="ob-confirm">✓ Telegram уже подключён</div>
      <div className="ob-help">ⓘ Вы можете продолжить без почты.</div>
      {error ? <Notice message={error} /> : null}
      <Actions back={back} busy={busy || save.isPending} disabled={profile.isPending || profile.isError} next={() => { void submit(); }} />
    </>
  );
}

function SubgroupStep({ staff, back, busy, next }: { staff: boolean; back?: () => void; busy: boolean; next: () => void }) {
  const queryClient = useQueryClient();
  const splits = useQuery({ queryKey: ['onboarding-subgroups'], queryFn: getStudentSplits, enabled: !staff });
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState('');
  const items = splits.data?.items ?? [];
  const missing = !staff && items.some(item => !item.selected_subgroup_id);
  async function select(split: string, selected: string) {
    setSaving(split);
    try {
      setError('');
      await selectStudentSplit(split, selected);
      await queryClient.invalidateQueries({ queryKey: ['onboarding-subgroups'] });
    } catch { setError('Не получилось сохранить подгруппу'); }
    finally { setSaving(null); }
  }
  return (
    <>
      <Title lead={staff ? 'Предметы и подгруппы добавляются старостой для студентов. Вы всегда видите все подгруппы.' :
        'Выберите свою подгруппу отдельно для каждого предмета, который староста разделил на подгруппы.'}>
        Подгруппы по предметам
      </Title>
      {staff ? <div className="ob-empty">Для роли старосты выбор подгрупп не требуется.</div> :
        splits.isPending ? <div className="ob-help">Загружаем предметы…</div> :
        splits.isError ? <Notice message="Не удалось загрузить подгруппы" retry={() => { void splits.refetch(); }} /> :
        items.length === 0 ? <div className="ob-empty">В вашей группе пока нет разделения по предметам. Можно продолжать.</div> :
        <><div className="ob-count">{items.length} предмета требуют выбора</div>
          <div className="ob-split-list">
            {items.map(item => <div className="ob-split" key={item.split_id}>
              <strong>{item.subject.name}</strong>
              <div className="ob-options" role="group" aria-label={'Подгруппа: ' + item.subject.name}>
                {item.options.map(option =>
                  <button type="button" key={option.id} disabled={saving !== null}
                    aria-pressed={item.selected_subgroup_id === option.id}
                    onClick={() => { void select(item.split_id, option.id); }}>{option.name}</button>)}
              </div>
            </div>)}
          </div></>}
      {missing ? <div className="ob-help">Для продолжения нужно выбрать подгруппу по каждому предмету.</div> : null}
      {error ? <Notice message={error} /> : null}
      <Actions back={back} busy={busy || saving !== null}
        disabled={!staff && (splits.isPending || splits.isError || missing)} label="Сохранить и продолжить" next={next} />
    </>
  );
}

async function ensureConsents(status: LegalStatus | undefined, checks: Checks, required: boolean, purposes: Array<keyof Checks>) {
  if (!status?.ready) {
    if (required) throw new Error('Юридические документы ещё не утверждены');
    return;
  }
  for (const purpose of purposes) {
    if (status[purpose]) continue;
    if (checks[purpose]) await acceptLegal(purpose, status.version);
    else if (required) throw new Error('Подтвердите согласие перед продолжением');
  }
}

function CredentialsStep({ back, busy, next, draft, onDraft }: {
  back?: () => void; busy: boolean; next: () => void;
  draft: SdoDraft | null; onDraft: (draft: SdoDraft | null) => void;
}) {
  const legal = useQuery({ queryKey: ['onboarding-legal'], queryFn: legalStatus });
  const profile = useProfileQuery();
  const [login, setLogin] = useState<string | null>(draft?.login ?? null);
  const [password, setPassword] = useState(draft?.password ?? '');
  const [agreeSdo, setAgreeSdo] = useState(Boolean(draft));
  const [error, setError] = useState('');
  const ready = legal.data?.ready === true;
  const loginValue = login ?? profile.data?.sdu.login ?? '';
  function submit() {
    if (!loginValue.trim() || (!password && !profile.data?.sdu.password_set)) {
      setError('Для подключения нужны логин и пароль. Или нажмите «Подключить позже».'); return;
    }
    if (!legal.data?.sdo && !agreeSdo) {
      setError('Для хранения реквизитов нужно отдельное согласие на СДО/ЛК.'); return;
    }
    setError('');
    onDraft({ login: loginValue.trim(), password });
    setPassword('');
    next();
  }
  return (
    <>
      <Title badge="Необязательно" lead="Подключите СДО и личный кабинет для учебных материалов. Этот шаг можно пропустить.">
        СДО и личный кабинет
      </Title>
      <div className="ob-form">
        <label>Логин СДО/ЛК<input type="text" autoComplete="off" maxLength={80} placeholder="Логин"
          value={loginValue} onChange={e => setLogin(e.target.value)} disabled={!ready} /></label>
        <label>Пароль СДО/ЛК<input type="password" autoComplete="new-password" maxLength={200}
          placeholder={profile.data?.sdu.password_set ? 'Сохранён — менять не обязательно' : 'Пароль'}
          value={password} onChange={e => setPassword(e.target.value)} disabled={!ready} /></label>
      </div>
      {ready ? <label className="ob-checkbox"><input type="checkbox" checked={legal.data?.sdo === true || agreeSdo}
        disabled={legal.data?.sdo === true} onChange={e => setAgreeSdo(e.target.checked)} />
        <span>Разрешаю хранить реквизиты СДО / ЛК <a href="/legal/sdo" target="_blank" rel="noopener noreferrer">Прочитать документ</a></span>
      </label> : <div className="ob-help">Подключение пока отключено до утверждения правовых документов.</div>}
      <div className="ob-help">🔒 Пароль сохраняется только после согласий на следующем шаге и не отправляется в чат Telegram.</div>
      {error ? <Notice message={error} /> : null}
      <div className="ob-link-row"><button type="button" disabled={busy} onClick={() => {
        onDraft(null);
        setPassword('');
        next();
      }}>Подключить позже</button></div>
      <Actions back={back} busy={busy} disabled={!ready || profile.isPending} next={submit} label="Продолжить" />
    </>
  );
}

function LegalStep({ required, back, busy, next, pendingSdo, clearSdo }: {
  required: boolean; back?: () => void; busy: boolean; next: () => void;
  pendingSdo: SdoDraft | null; clearSdo: () => void;
}) {
  const legal = useQuery({ queryKey: ['onboarding-legal'], queryFn: legalStatus });
  const saveProfile = useUpdateProfile();
  const [checks, setChecks] = useState<Checks>({ terms: false, service: false, sdo: false });
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  async function submit() {
    setSending(true);
    try {
      setError('');
      await ensureConsents(legal.data, checks, required || Boolean(pendingSdo), ['terms', 'service']);
      if (pendingSdo) {
        if (!legal.data?.ready) throw new Error('Для подключения СДО нужны опубликованные документы');
        if (!legal.data.sdo) await acceptLegal('sdo', legal.data.version);
        await saveProfile.mutateAsync({
          sdu_platform: 'РАНХиГС — СДО / ЛК', sdu_login: pendingSdo.login,
          ...(pendingSdo.password ? { sdu_password: pendingSdo.password } : {}),
        });
        clearSdo();
      }
      next();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось подтвердить документы'); }
    finally { setSending(false); }
  }
  return (
    <>
      <Title lead="Ознакомьтесь с документами и подтвердите необходимые согласия. Отметки не установлены заранее.">
        Почти готово
      </Title>
      <div className="ob-consents">
        {([['terms', 'Принимаю условия использования'], ['service', 'Даю согласие на обработку персональных данных']] as const)
          .map(([kind, label]) => <label key={kind} className="ob-checkbox">
            <input type="checkbox" checked={legal.data?.[kind] === true || checks[kind]}
              disabled={!legal.data?.ready || legal.data?.[kind] === true}
              onChange={e => setChecks({ ...checks, [kind]: e.target.checked })} />
            <span>{label}<a href={'/legal/' + kind} target="_blank" rel="noopener noreferrer">Прочитать документ ↗</a></span>
          </label>)}
      </div>
      {pendingSdo ? <div className="ob-help">Данные СДО будут сохранены только после подтверждения этих документов.</div> : null}
      {!legal.data?.ready ? <div className="ob-help">Документы пока находятся на утверждении. Обязательные согласия станут доступны после публикации.</div> : null}
      {error ? <Notice message={error} /> : null}
      <Actions back={back} busy={busy || sending} disabled={legal.isPending || legal.isError ||
        ((required || Boolean(pendingSdo)) && !legal.data?.ready)}
        next={() => { void submit(); }} label="Продолжить" />
    </>
  );
}

function AppearanceStep({ back, busy, next }: { back?: () => void; busy: boolean; next: () => void }) {
  // Exact same state, cookies and setters as the existing Profile → Personalization screen.
  const { preference, setTheme, accent, setAccent } = useTelegram();
  const [display, setDisplay] = useDisplay();
  return (
    <>
      <Title lead="Последний штрих: выберите оформление, в котором вам комфортнее учиться. Всё можно поменять в профиле.">
        Подстроим приложение под вас
      </Title>
      <div className="ob-setting">
        <strong>Тема</strong>
        <div className="ob-options" role="group" aria-label="Тема">
          {THEMES.map(([value, label]) => <button key={String(value)} type="button"
            aria-pressed={preference === value} onClick={() => setTheme(value)}>{label}</button>)}
        </div>
      </div>
      <div className="ob-setting">
        <strong>Цвет акцента</strong>
        <div className="ob-swatches" role="group" aria-label="Цвет акцента">
          {ACCENTS.map(id => {
            const info = ACCENT_INFO[id];
            const style = { ['--swatch-h' as string]: info.h, ['--swatch-s' as string]: info.s,
              ['--swatch-l' as string]: info.l } as CSSProperties;
            return <button type="button" key={id} style={style} aria-label={info.name} title={info.name}
              aria-pressed={accent === id} onClick={() => setAccent(id)}><i /></button>;
          })}
        </div>
      </div>
      <div className="ob-setting">
        <strong>Размер текста</strong>
        <div className="ob-options" role="group" aria-label="Размер текста">
          {TEXT_SIZES.map(([value, label]) => <button key={value} type="button" aria-pressed={display.text === value}
            onClick={() => setDisplay({ text: value })}>{label}</button>)}
        </div>
      </div>
      <div className="ob-setting ob-extra">
        <strong>Плотность списков</strong>
        <div className="ob-options" role="group" aria-label="Плотность списков">
          {DENSITIES.map(([value, label]) => <button key={value} type="button" aria-pressed={display.density === value}
            onClick={() => setDisplay({ density: value })}>{label}</button>)}
        </div>
      </div>
      <label className="ob-motion">Анимации <input type="checkbox" checked={display.motion}
        onChange={e => setDisplay({ motion: e.target.checked })} /></label>
      <Actions back={back} busy={busy} next={next} label="Сохранить оформление" />
    </>
  );
}
