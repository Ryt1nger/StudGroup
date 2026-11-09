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
import { ACCENTS, useTelegram, type Accent } from '../../telegram/TelegramProvider';
import { useDisplay } from '../../shell/display';
import { ACCENT_INFO, THEMES, TEXT_SIZES, DENSITIES } from '../../shell/appearanceOptions';
import { OnboardingPreviewContext } from './OnboardingPreviewContext';
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

function Shell({ children, role, group, step, preview, onClose, previewTheme, previewAccent, previewText }: {
  children: ReactNode; role: string; group: string; step: Step;
  preview?: boolean; onClose?: () => void;
  previewTheme?: 'light' | 'dark' | null; previewAccent?: Accent; previewText?: 's' | 'm' | 'l';
}) {
  const index = steps.indexOf(step);
  const { scheme } = useTelegram();
  const activeScheme = preview && previewTheme ? previewTheme : scheme;
  const art = ART[step][activeScheme === 'dark' ? 1 : 0];
  const accentInfo = preview && previewAccent ? ACCENT_INFO[previewAccent] : null;
  const previewStyle = accentInfo ? {
    '--ob-active': `hsl(${accentInfo.h} ${accentInfo.s} ${accentInfo.l})`,
    '--ob-active-text': accentInfo.on ?? '#fff',
  } as CSSProperties : undefined;
  return (
    <div className={'ob-root' + (preview ? ' ob-preview' : '')}
      data-ob-preview-theme={preview ? activeScheme : undefined}
      data-ob-preview-text={preview ? previewText : undefined}
      style={previewStyle}>
      <main className="ob-card">
        <header className="ob-topline">
          <div className="ob-brand">
            <img src={`${import.meta.env.BASE_URL}studgroup-logo.png`} width="42" height="42" alt="" />
            <span>StudGroup</span>
          </div>
          <span className="ob-campus">{group}</span>
        </header>
        <div className="ob-meta-line">
          <span className="ob-role" role={preview ? 'status' : undefined}>
            {preview ? 'Предпросмотр · данные не сохраняются' : `ПЕРВЫЙ ЗАПУСК · ${role}`}
          </span>
          {preview ? <button className="ob-preview-close" type="button" onClick={onClose}>Закрыть ✕</button> : null}
        </div>
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
  const [preview, setPreview] = useState(false);
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
  if (progress.data.completed) {
    if (preview) return <PreviewWizard state={progress.data} onClose={() => setPreview(false)} />;
    return <OnboardingPreviewContext.Provider value={() => setPreview(true)}>{children}</OnboardingPreviewContext.Provider>;
  }
  return <Wizard state={progress.data} membershipId={membershipId} />;
}


/**
 * A read-only, client-side tour of the exact onboarding layout for people who
 * have already completed setup. No progress, profile, subgroup, credential or
 * legal writes are ever invoked. It never changes the server's completion flag.
 */
function PreviewWizard({ state, onClose }: { state: OnboardingState; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const [previewTheme, setPreviewTheme] = useState<'light' | 'dark' | null>(null);
  const [previewAccent, setPreviewAccent] = useState<Accent>('violet');
  const [previewText, setPreviewText] = useState<'s' | 'm' | 'l'>('m');
  const [previewDensity, setPreviewDensity] = useState<'normal' | 'compact'>('normal');
  const [previewMotion, setPreviewMotion] = useState(true);
  const [first, setFirst] = useState<string | null>(null);
  const [last, setLast] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [localSplitChoices, setLocalSplitChoices] = useState<Record<string, string>>({});
  const [subjectPage, setSubjectPage] = useState(0);
  const [terms, setTerms] = useState(false);
  const [service, setService] = useState(false);
  const profile = useProfileQuery();
  const staff = state.role !== 'student';
  const splits = useQuery({ queryKey: ['onboarding-subgroups-preview'], queryFn: getStudentSplits, enabled: !staff });
  const step = steps[index]!;
  const next = () => setIndex(n => Math.min(steps.length - 1, n + 1));
  const back = index > 0 ? () => setIndex(n => Math.max(0, n - 1)) : undefined;
  const subjectPageCount = staff ? 1 : Math.max(1, Math.ceil((splits.data?.items.length ?? 0) / 2));
  const onNext = () => {
    if (step === 'subgroups' && subjectPage < subjectPageCount - 1) setSubjectPage(page => page + 1);
    else if (step === 'finish') onClose();
    else next();
  };
  const onBack = step === 'subgroups' && subjectPage > 0
    ? () => setSubjectPage(page => page - 1) : back;
  const previewActions = (label = 'Далее') =>
    <Actions back={onBack} label={label} next={onNext} />;
  const currentFirst = first ?? profile.data?.first_name ?? '';
  const currentLast = last ?? profile.data?.last_name ?? '';
  const currentEmail = email ?? profile.data?.email ?? '';

  return <Shell step={step} group={state.group_name}
    role={staff ? (state.role === 'headman' ? 'СТАРОСТА' : 'ЗАМЕСТИТЕЛЬ') : 'СТУДЕНТ'}
    preview onClose={onClose} previewTheme={previewTheme} previewAccent={previewAccent} previewText={previewText}>
    {step === 'welcome' ? <>
      <Title lead="Расписание, задания и события группы — в одном месте.">Настроим StudGroup под вас</Title>
      <div className="ob-feature-list">
        <div><span className="ob-feature-icon">▦</span><span><strong>Удобное расписание</strong><small>Пары и важные изменения</small></span></div>
        <div><span className="ob-feature-icon">☑</span><span><strong>Задания и дедлайны</strong><small>Всё важное по предметам</small></span></div>
        <div><span className="ob-feature-icon">♧</span><span><strong>События и объявления</strong><small>Новости вашей группы в одном месте</small></span></div>
      </div>
      {previewActions('Начать настройку')}
    </> : null}
    {step === 'personal' ? <>
      <Title lead="Ваше имя будет видно в профиле и карточках.">Как к вам обращаться?</Title>
      <div className="ob-form">
        <label>Имя <span className="ob-required">*</span>
          <input type="text" autoComplete="off" maxLength={60} value={currentFirst}
            placeholder="Ваше имя" onChange={e => setFirst(e.target.value)} /></label>
        <label>Фамилия <small>необязательно</small>
          <input type="text" autoComplete="off" maxLength={60} value={currentLast}
            placeholder="Ваша фамилия" onChange={e => setLast(e.target.value)} /></label>
      </div>
      <div className="ob-help">ⓘ Это демонстрация формы. Ваш профиль не изменится.</div>
      {previewActions()}
    </> : null}
    {step === 'contacts' ? <>
      <Title lead="Почта для связи необязательна. Telegram уже подключён.">Контакты для связи</Title>
      <div className="ob-form"><label>Почта <small>необязательно</small>
        <input type="email" autoComplete="off" value={currentEmail}
          placeholder="name@example.com" maxLength={120} onChange={e => setEmail(e.target.value)} /></label></div>
      <div className="ob-confirm">✓ Telegram уже подключён</div>
      <div className="ob-help">ⓘ Изменения почты здесь не сохраняются.</div>
      {previewActions()}
    </> : null}
    {step === 'subgroups' ? <>
      <Title lead="Укажите подгруппу для каждого предмета.">Подгруппы по предметам</Title>
      {staff ? <>
        <div className="ob-help">Для старосты выбор подгрупп не требуется. Ниже — пример экрана студента.</div>
        <div className="ob-count">Пример: 2 предмета</div>
        <div className="ob-split-list">
          {[
            { id: 'english', subject: 'Английский язык', options: ['1', '2'] },
            { id: 'informatics', subject: 'Информатика', options: ['1', '2'] },
          ].map(item => <div className="ob-split" key={item.id}>
            <strong>{item.subject}</strong>
            <div className="ob-options" role="group" aria-label={'Пример: ' + item.subject}>
              {item.options.map(option =>
                <button type="button" key={option}
                  aria-pressed={(localSplitChoices[item.id] ?? '1') === option}
                  onClick={() => setLocalSplitChoices(current => ({ ...current, [item.id]: option }))}>{option}</button>)}
            </div>
          </div>)}
        </div>
      </> :
      splits.isPending ? <div className="ob-help">Загружаем предметы…</div> :
      splits.isError ? <div className="ob-help">Не удалось загрузить список предметов для предпросмотра.</div> :
      (splits.data?.items ?? []).length === 0 ? <div className="ob-empty">В вашей группе пока нет разделения по предметам.</div> :
      <><div className="ob-count">{splits.data?.items.length ?? 0} предметов · экран {subjectPage + 1} из {subjectPageCount}</div>
        <div className="ob-split-list">
          {splits.data?.items.slice(subjectPage * 2, (subjectPage + 1) * 2).map(item => <div className="ob-split" key={item.split_id}>
            <strong>{item.subject.name}</strong>
            <div className="ob-options" role="group" aria-label={'Подгруппа: ' + item.subject.name}>
              {item.options.map(option =>
                <button type="button" key={option.id}
                  aria-pressed={(localSplitChoices[item.split_id] ?? item.selected_subgroup_id) === option.id}
                  onClick={() => setLocalSplitChoices(current => ({ ...current, [item.split_id]: option.id }))}>
                  {option.name}
                </button>)}
            </div>
          </div>)}
        </div></>}
      {previewActions('Сохранить и продолжить')}
    </> : null}
    {step === 'sdo' ? <>
      <Title badge="Необязательно" lead="Подключение СДО можно настроить позже в профиле.">
        СДО и личный кабинет
      </Title>
      <div className="ob-form">
        <label>Логин СДО/ЛК<input type="text" placeholder="Логин" disabled /></label>
        <label>Пароль СДО/ЛК<input type="password" placeholder="Пароль" disabled /></label>
      </div>
      <div className="ob-help">🔒 В предпросмотре ввод реквизитов отключён. Подключение доступно в профиле.</div>
      {previewActions('Продолжить')}
    </> : null}
    {step === 'legal' ? <>
      <Title lead="Ознакомьтесь с документами. Отметки не проставлены заранее.">Почти готово</Title>
      <div className="ob-consents">
        <label className="ob-checkbox"><input type="checkbox" checked={terms} onChange={e => setTerms(e.target.checked)} />
          <span>Принимаю условия использования <span className="ob-preview-document">Прочитать документ ↗</span></span></label>
        <label className="ob-checkbox"><input type="checkbox" checked={service} onChange={e => setService(e.target.checked)} />
          <span>Даю согласие на обработку персональных данных <span className="ob-preview-document">Прочитать документ ↗</span></span></label>
      </div>
      <div className="ob-help">Это демонстрация. Отметки не записываются и согласия не отправляются.</div>
      {previewActions('Продолжить')}
    </> : null}
    {step === 'appearance' ? <>
      <Title lead="Выберите оформление. Его всегда можно изменить в профиле.">
        Подстроим приложение под вас
      </Title>
      <div className="ob-setting"><strong>Тема</strong>
        <div className="ob-options" role="group" aria-label="Тема">
          {THEMES.map(([value, label]) => <button key={String(value)} type="button"
            aria-pressed={previewTheme === value} onClick={() => setPreviewTheme(value)}>{label}</button>)}
        </div>
      </div>
      <div className="ob-setting"><strong>Цвет акцента</strong>
        <div className="ob-swatches" role="group" aria-label="Цвет акцента">
          {ACCENTS.map(id => {
            const info = ACCENT_INFO[id];
            const style = { ['--swatch-h' as string]: info.h, ['--swatch-s' as string]: info.s,
              ['--swatch-l' as string]: info.l } as CSSProperties;
            return <button type="button" key={id} style={style} aria-label={info.name} title={info.name}
              aria-pressed={previewAccent === id} onClick={() => setPreviewAccent(id)}><i /></button>;
          })}
        </div>
      </div>
      <div className="ob-setting"><strong>Размер текста</strong>
        <div className="ob-options" role="group" aria-label="Размер текста">
          {TEXT_SIZES.map(([value, label]) => <button type="button" key={value}
            aria-pressed={previewText === value} onClick={() => setPreviewText(value)}>{label}</button>)}
        </div>
      </div>
      <div className="ob-setting ob-extra"><strong>Плотность списков</strong>
        <div className="ob-options" role="group" aria-label="Плотность списков">
          {DENSITIES.map(([value, label]) => <button type="button" key={value}
            aria-pressed={previewDensity === value} onClick={() => setPreviewDensity(value)}>{label}</button>)}
        </div>
      </div>
      <label className="ob-motion">Анимации <input type="checkbox" checked={previewMotion}
        onChange={e => setPreviewMotion(e.target.checked)} /></label>
      <div className="ob-help">Выбор действует только внутри предпросмотра.</div>
      {previewActions('Сохранить оформление')}
    </> : null}
    {step === 'finish' ? <>
      <Title lead="Расписание, задания и события уже ждут вас.">
        Настройка завершена!
      </Title>
      <div className="ob-done-lines">
        <span><b>✓</b> Группа: {state.group_name}</span>
        <span><b>✓</b> Личные данные сохранены</span>
        <span><b>✓</b> Оформление настроено</span>
      </div>
      {previewActions('Вернуться в приложение')}
    </> : null}
  </Shell>;
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
          <Title lead="Расписание, задания и события группы — в одном месте.">Настроим StudGroup под вас</Title>
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
          <Title lead="Расписание, задания и события уже ждут вас.">Настройка завершена!</Title>
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
      <Title lead="Ваше имя будет видно в профиле и карточках.">Как к вам обращаться?</Title>
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
      <Title lead="Почта для связи необязательна. Telegram уже подключён.">Контакты для связи</Title>
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
  const [pageIndex, setPageIndex] = useState(0);
  const [error, setError] = useState('');
  const items = splits.data?.items ?? [];
  const pageCount = Math.max(1, Math.ceil(items.length / 2));
  const page = Math.min(pageIndex, pageCount - 1);
  const visibleItems = items.slice(page * 2, page * 2 + 2);
  const missingVisible = visibleItems.some(item => !item.selected_subgroup_id);
  const missingAny = items.some(item => !item.selected_subgroup_id);
  const hasNextPage = !staff && page + 1 < pageCount;

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
      <Title lead={staff ? 'Подгруппы нужны только студентам.' : 'Укажите свою подгруппу по каждому предмету.'}>
        Подгруппы по предметам
      </Title>
      {staff ? <div className="ob-empty">Вы видите все подгруппы. Этот шаг можно пропустить.</div> :
        splits.isPending ? <div className="ob-help">Загружаем предметы…</div> :
        splits.isError ? <Notice message="Не удалось загрузить подгруппы" retry={() => { void splits.refetch(); }} /> :
        items.length === 0 ? <div className="ob-empty">В группе пока нет предметов с подгруппами.</div> :
        <>
          <div className="ob-count">{items.length} предметов · {page + 1} из {pageCount}</div>
          <div className="ob-split-list">
            {visibleItems.map(item => <div className="ob-split" key={item.split_id}>
              <strong>{item.subject.name}</strong>
              <div className="ob-options" role="group" aria-label={'Подгруппа: ' + item.subject.name}>
                {item.options.map(option =>
                  <button type="button" key={option.id} disabled={saving !== null}
                    aria-pressed={item.selected_subgroup_id === option.id}
                    onClick={() => { void select(item.split_id, option.id); }}>{option.name}</button>)}
              </div>
            </div>)}
          </div>
          {missingVisible ? <p className="ob-subject-nav">Выберите подгруппу для каждого предмета на экране.</p> : null}
        </>}
      {error ? <Notice message={error} /> : null}
      <Actions back={page > 0 && !staff ? () => setPageIndex(page - 1) : back}
        busy={busy || saving !== null}
        disabled={!staff && (splits.isPending || splits.isError || missingVisible || (!hasNextPage && missingAny))}
        label={hasNextPage ? 'Следующие предметы' : 'Сохранить и продолжить'}
        next={hasNextPage ? () => setPageIndex(page + 1) : next} />
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
      <Title badge="Необязательно" lead="СДО можно подключить позже в профиле.">
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
      <Title lead="Ознакомьтесь с документами перед продолжением.">
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
      <Title lead="Выберите оформление. Его всегда можно изменить в профиле.">
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
