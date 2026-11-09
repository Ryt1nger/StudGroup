import { useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useProfileQuery, useUpdateProfile } from '../../api/queries';
import { acceptLegal, legalStatus, type LegalStatus } from '../../api/legal';
import {
  completeOnboarding, getOnboarding, getStudentSplits, selectStudentSplit,
  setOnboardingStep, type OnboardingState,
} from '../../api/onboarding';
import { useSession } from '../../session/SessionProvider';
import './onboarding.css';

const steps = ['welcome', 'legal', 'personal', 'subgroups', 'sdo', 'finish'] as const;
type StudentStep = (typeof steps)[number];
type ConsentChecks = { terms: boolean; service: boolean; sdo: boolean };

function Shell({ children, role, group }: { children: ReactNode; role: string; group: string }) {
  return (
    <div className="ob-root">
      <div className="ob-glow" aria-hidden="true" />
      <main className="ob-card">
        <header className="ob-topline">
          <span className="ob-brand"><span className="ob-logo">S</span> StudGroup</span>
          <span className="ob-campus">{group}</span>
        </header>
        <div className="ob-role">{role}</div>
        {children}
        <footer className="ob-footer">Ваш прогресс сохраняется в StudGroup. Можно закрыть приложение и продолжить позже.</footer>
      </main>
    </div>
  );
}

function Notice({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="ob-notice" role="alert">{message}{retry ? <button type="button" onClick={retry}>Повторить</button> : null}</div>;
}

function Actions({ back, next, busy = false, disabled = false, label = 'Продолжить' }: {
  back?: () => void;
  next: () => void;
  busy?: boolean;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <div className="ob-actions">
      {back ? <button type="button" className="ob-button ob-secondary" disabled={busy} onClick={back}>Назад</button> : null}
      <button type="button" className="ob-button ob-primary" disabled={busy || disabled} onClick={next}>
        {busy ? 'Сохраняем…' : label}<span aria-hidden="true"> →</span>
      </button>
    </div>
  );
}

function Loading({ message = 'Загружаем ваш прогресс…' }: { message?: string }) {
  return <Shell role="Знакомство" group="Ваше пространство"><div className="ob-loading" role="status"><span className="ob-spinner" />{message}</div></Shell>;
}

export function OnboardingGate({ children }: { children: ReactNode }) {
  const { state } = useSession();
  const active = state.status === 'ready' && state.session.access_state === 'active';
  const isDemo = import.meta.env.MODE === 'demo';
  const id = active ? state.session.membership?.id : null;
  const progress = useQuery({
    queryKey: ['first-run', id],
    queryFn: getOnboarding,
    enabled: Boolean(id) && !isDemo,
    retry: 1,
    staleTime: 0,
  });
  if (!active || !id || isDemo) return <>{children}</>;
  if (progress.isPending) return <Loading />;
  if (progress.isError || !progress.data) {
    return <Shell role="Подключение" group="StudGroup"><Notice message="Не удалось восстановить регистрацию. Ничего не сброшено." retry={() => { void progress.refetch(); }} /></Shell>;
  }
  if (progress.data.completed) return <>{children}</>;
  return progress.data.role === 'student'
    ? <StudentWizard state={progress.data} membershipId={id} />
    : <StaffFirstRun state={progress.data} membershipId={id} />;
}

function useMove(membershipId: string) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function move(step: StudentStep) {
    setBusy(true);
    setError('');
    try {
      await setOnboardingStep(step);
      await queryClient.invalidateQueries({ queryKey: ['first-run', membershipId] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить шаг');
    } finally {
      setBusy(false);
    }
  }
  async function finish() {
    setBusy(true);
    setError('');
    try {
      await completeOnboarding();
      await queryClient.invalidateQueries({ queryKey: ['first-run', membershipId] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось завершить регистрацию');
    } finally {
      setBusy(false);
    }
  }
  return { move, finish, busy, error, setError };
}

function LegalFields({ status, required, checks, setChecks, includeSdo = false }: {
  status: LegalStatus | undefined;
  required: boolean;
  checks: ConsentChecks;
  setChecks: (next: ConsentChecks) => void;
  includeSdo?: boolean;
}) {
  const entries: Array<[keyof ConsentChecks, string, 'terms' | 'service' | 'sdo']> = [
    ['terms', 'Принимаю условия использования', 'terms'],
    ['service', 'Даю отдельное согласие на обработку персональных данных', 'service'],
  ];
  if (includeSdo) entries.push(['sdo', 'Добровольно разрешаю хранить мои реквизиты СДО / ЛК', 'sdo']);
  return (
    <div className="ob-consents">
      <p className="ob-hint">Документы подтверждаются отдельно. Отметки не выставлены заранее.</p>
      <p className="ob-doc"><a href="/legal/privacy" target="_blank" rel="noopener noreferrer">Политика обработки персональных данных</a></p>
      {entries.map(([key, label, document]) => (
        <label key={key} className="ob-consent">
          <input type="checkbox" checked={status?.[key] === true || checks[key]}
            disabled={status?.[key] === true || !status?.ready}
            onChange={(event) => setChecks({ ...checks, [key]: event.target.checked })} />
          <span>{label}<a href={'/legal/' + document} target="_blank" rel="noopener noreferrer">Прочитать документ</a></span>
        </label>
      ))}
      {!status?.ready ? (
        <p className="ob-subtle" role="status">
          {required
            ? 'Юридические документы ещё не утверждены оператором. Подтверждение недоступно до их публикации.'
            : 'Правовые документы находятся на утверждении; необязательное подключение СДО/ЛК пока отключено.'}
        </p>
      ) : null}
    </div>
  );
}

async function ensureConsents(status: LegalStatus | undefined, checks: ConsentChecks, required: boolean, includeSdo: boolean) {
  if (!status?.ready) {
    if (required || includeSdo) throw new Error('Документы оператора ещё не утверждены');
    return;
  }
  const purposes: (keyof ConsentChecks)[] = includeSdo ? ['terms', 'service', 'sdo'] : ['terms', 'service'];
  for (const purpose of purposes) {
    if (status[purpose]) continue;
    if (checks[purpose]) await acceptLegal(purpose, status.version);
    else if (required || includeSdo) throw new Error('Необходимо подтвердить каждый документ отдельно');
  }
}

function StudentWizard({ state, membershipId }: { state: OnboardingState; membershipId: string }) {
  const stage = state.step === 'complete' ? 'finish' : state.step;
  const index = steps.indexOf(stage);
  const progress = useMove(membershipId);
  const legal = useQuery({ queryKey: ['onboarding-legal'], queryFn: legalStatus, retry: 1 });
  const [checks, setChecks] = useState<ConsentChecks>({ terms: false, service: false, sdo: false });

  async function legalNext() {
    progress.setError('');
    try {
      await ensureConsents(legal.data, checks, state.legal_required, false);
      await progress.move('personal');
    } catch (cause) {
      progress.setError(cause instanceof Error ? cause.message : 'Не удалось подтвердить документы');
    }
  }
  const back = index > 0 ? () => { void progress.move(steps[index - 1]!); } : undefined;
  return (
    <Shell role="Знакомство · студент" group={state.group_name}>
      <div className="ob-progress" aria-label={'Шаг ' + (index + 1) + ' из ' + steps.length}>
        {steps.map((name, i) => <span key={name} className={'ob-progress-segment ' + (i <= index ? 'is-active' : '')} />)}
      </div>
      <div className="ob-stepnote">Шаг {index + 1} из {steps.length}</div>
      <section className="ob-scene" key={stage}>
        {stage === 'welcome' ? (
          <>
            <div className="ob-illustration">✦</div>
            <h1>Твоя учёба.<br /><em>Всё по полочкам.</em></h1>
            <p className="ob-lead">Привет! Ты уже в группе <strong>{state.group_name}</strong>. Теперь настроим твой StudGroup под тебя — без длинных анкет и лишних сообщений в чате.</p>
            <div className="ob-feature-row">
              <span>📚 Задания</span><span>🗓️ Расписание</span><span>🔔 Напоминания</span>
            </div>
            <Actions busy={progress.busy} label="Начать знакомство" next={() => { void progress.move('legal'); }} />
          </>
        ) : null}
        {stage === 'legal' ? (
          <>
            <div className="ob-icon">🔒</div>
            <h1>Сначала о данных</h1>
            <p className="ob-lead">Твои сведения принадлежат тебе. Ознакомься с документами и отдельно подтверди то, что необходимо для работы сервиса.</p>
            {legal.isError ? <Notice message="Не удалось загрузить состояние согласий" retry={() => { void legal.refetch(); }} /> : (
              <LegalFields status={legal.data} required={state.legal_required} checks={checks} setChecks={setChecks} />
            )}
            <Actions back={back} busy={progress.busy} disabled={legal.isPending || legal.isError || (state.legal_required && !legal.data?.ready)}
              next={() => { void legalNext(); }} />
          </>
        ) : null}
        {stage === 'personal' ? (
          <PersonalStep back={back} busy={progress.busy} next={() => progress.move('subgroups')} />
        ) : null}
        {stage === 'subgroups' ? (
          <SubgroupStep back={back} busy={progress.busy} next={() => progress.move('sdo')} />
        ) : null}
        {stage === 'sdo' ? (
          <CredentialsStep back={back} busy={progress.busy} next={() => progress.move('finish')} />
        ) : null}
        {stage === 'finish' ? (
          <>
            <div className="ob-illustration ob-success">✓</div>
            <h1>Всё готово!</h1>
            <p className="ob-lead">Теперь StudGroup будет показывать твои задания, расписание и уведомления. Параметры можно изменить в профиле.</p>
            <div className="ob-done-lines"><span>✓ Группа подтверждена</span><span>✓ Личные настройки сохранены</span><span>✓ Можно приступать к учёбе</span></div>
            <Actions back={back} busy={progress.busy} label="Открыть StudGroup" next={() => { void progress.finish(); }} />
          </>
        ) : null}
        {progress.error ? <Notice message={progress.error} /> : null}
      </section>
    </Shell>
  );
}

function PersonalStep({ back, busy, next }: { back?: () => void; busy: boolean; next: () => Promise<void> }) {
  const profile = useProfileQuery();
  const save = useUpdateProfile();
  const [first, setFirst] = useState<string | null>(null);
  const [last, setLast] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [error, setError] = useState('');
  const firstValue = first ?? profile.data?.first_name ?? '';
  const lastValue = last ?? profile.data?.last_name ?? '';
  const emailValue = email ?? profile.data?.email ?? '';

  async function submit() {
    if (!firstValue.trim()) { setError('Укажи своё имя'); return; }
    setError('');
    try {
      await save.mutateAsync({
        first_name: firstValue.trim(),
        last_name: lastValue.trim() || null,
        email: emailValue.trim() || null,
      });
      await next();
    } catch {
      setError('Не удалось сохранить профиль. Проверь соединение.');
    }
  }
  return (
    <>
      <div className="ob-icon">👋</div>
      <h1>Давай знакомиться</h1>
      <p className="ob-lead">Как к тебе обращаться? Достаточно имени, а остальные данные необязательны.</p>
      {profile.isPending ? <p className="ob-subtle">Загружаем профиль…</p> : (
        <form className="ob-form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <label>Имя <span className="ob-required">*</span><input type="text" autoComplete="given-name" maxLength={60}
            placeholder="Твоё имя" value={firstValue} onChange={(e) => setFirst(e.target.value)} required /></label>
          <label>Фамилия <small>по желанию</small><input type="text" autoComplete="family-name" maxLength={60}
            placeholder="Твоя фамилия" value={lastValue} onChange={(e) => setLast(e.target.value)} /></label>
          <label>Почта <small>по желанию</small><input type="email" autoComplete="email" maxLength={120}
            placeholder="name@example.com" value={emailValue} onChange={(e) => setEmail(e.target.value)} /></label>
        </form>
      )}
      {error ? <Notice message={error} /> : null}
      <Actions back={back} busy={busy || save.isPending} disabled={profile.isPending || profile.isError} next={() => { void submit(); }} />
    </>
  );
}

function SubgroupStep({ back, busy, next }: { back?: () => void; busy: boolean; next: () => Promise<void> }) {
  const queryClient = useQueryClient();
  const splits = useQuery({ queryKey: ['onboarding-subgroups'], queryFn: getStudentSplits });
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState('');
  const items = splits.data?.items ?? [];
  const missing = items.some(item => !item.selected_subgroup_id);

  async function select(split: string, selected: string) {
    setSaving(split);
    setError('');
    try {
      await selectStudentSplit(split, selected);
      await queryClient.invalidateQueries({ queryKey: ['onboarding-subgroups'] });
    } catch {
      setError('Не получилось сохранить выбор подгруппы');
    } finally {
      setSaving(null);
    }
  }
  return (
    <>
      <div className="ob-icon">🧩</div>
      <h1>Твои подгруппы</h1>
      <p className="ob-lead">Выбери подгруппу для каждого предмета. Тогда задания будут показываться именно тебе.</p>
      {splits.isPending ? <p className="ob-subtle">Загружаем предметы…</p> : null}
      {splits.isError ? <Notice message="Не удалось получить подгруппы" retry={() => { void splits.refetch(); }} /> : null}
      {!splits.isPending && !splits.isError && items.length === 0 ? (
        <div className="ob-empty">✨ В твоей группе пока нет разделения на подгруппы. Можно идти дальше!</div>
      ) : (
        <div className="ob-split-list">
          {items.map((item, i) => (
            <label className="ob-split" key={item.split_id}>
              <span className="ob-split-number">{i + 1}</span>
              <span className="ob-split-body"><strong>{item.subject.name}</strong>
                <select aria-label={'Подгруппа: ' + item.subject.name}
                  value={item.selected_subgroup_id ?? ''} disabled={saving !== null}
                  onChange={(e) => { void select(item.split_id, e.target.value); }}>
                  <option value="" disabled>Выбрать подгруппу</option>
                  {item.options.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}
                </select>
              </span>
            </label>
          ))}
        </div>
      )}
      {error ? <Notice message={error} /> : null}
      {missing ? <p className="ob-subtle">Выбери все подгруппы, чтобы продолжить.</p> : null}
      <Actions back={back} busy={busy || saving !== null} disabled={splits.isPending || splits.isError || missing}
        next={() => { void next(); }} />
    </>
  );
}

function CredentialsStep({ back, busy, next }: { back?: () => void; busy: boolean; next: () => Promise<void> }) {
  const legal = useQuery({ queryKey: ['onboarding-legal'], queryFn: legalStatus });
  const profile = useProfileQuery();
  const save = useUpdateProfile();
  const [login, setLogin] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [checks, setChecks] = useState<ConsentChecks>({ terms: false, service: false, sdo: false });
  const [error, setError] = useState('');
  const ready = legal.data?.ready === true;

  async function submit() {
    if (!login?.trim() && !profile.data?.sdu.login) { setError('Введи логин СДО/ЛК'); return; }
    if (!password && !profile.data?.sdu.password_set) { setError('Введи пароль СДО/ЛК'); return; }
    setError('');
    try {
      await ensureConsents(legal.data, checks, true, true);
      await save.mutateAsync({
        sdu_platform: 'РАНХиГС — СДО / ЛК',
        sdu_login: (login ?? profile.data?.sdu.login ?? '').trim(),
        ...(password ? { sdu_password: password } : {}),
      });
      setPassword('');
      await next();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить данные');
    }
  }
  return (
    <>
      <div className="ob-icon">🔐</div>
      <h1>СДО и личный кабинет</h1>
      <p className="ob-lead">Для обеих систем используется одна пара логин и пароль. Это <strong>необязательный</strong> шаг — можно подключить позже.</p>
      <div className="ob-form">
        <label>Логин СДО / ЛК<input type="text" autoComplete="off" maxLength={80}
          placeholder="Твой логин" value={login ?? profile.data?.sdu.login ?? ''} onChange={(e) => setLogin(e.target.value)}
          disabled={!ready} /></label>
        <label>Пароль СДО / ЛК<input type="password" autoComplete="new-password" maxLength={200}
          placeholder={profile.data?.sdu.password_set ? 'Пароль уже сохранён' : 'Твой пароль'}
          value={password} onChange={(e) => setPassword(e.target.value)} disabled={!ready} /></label>
        {ready ? <LegalFields status={legal.data} required checks={checks} setChecks={setChecks} includeSdo /> :
          <p className="ob-subtle">Сохранение пароля пока отключено до утверждения документов оператора.</p>}
      </div>
      <p className="ob-subtle">Пароль не отправляется в Telegram и не показывается другим участникам. Сохранение не означает, что интеграция с вузом уже работает.</p>
      {error ? <Notice message={error} /> : null}
      <div className="ob-actions">
        {back ? <button type="button" className="ob-button ob-secondary" disabled={busy || save.isPending} onClick={back}>Назад</button> : null}
        <button type="button" className="ob-button ob-secondary" disabled={busy || save.isPending}
          onClick={() => { void next(); }}>Пропустить</button>
        <button type="button" className="ob-button ob-primary" disabled={!ready || busy || save.isPending || profile.isPending}
          onClick={() => { void submit(); }}>Сохранить и далее →</button>
      </div>
    </>
  );
}

function StaffFirstRun({ state, membershipId }: { state: OnboardingState; membershipId: string }) {
  const progress = useMove(membershipId);
  const queryClient = useQueryClient();
  const profile = useProfileQuery();
  const save = useUpdateProfile();
  const legal = useQuery({ queryKey: ['onboarding-legal'], queryFn: legalStatus });
  const [first, setFirst] = useState<string | null>(null);
  const [last, setLast] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [login, setLogin] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [checks, setChecks] = useState<ConsentChecks>({ terms: false, service: false, sdo: false });
  const [error, setError] = useState('');
  const firstValue = first ?? profile.data?.first_name ?? '';
  const lastValue = last ?? profile.data?.last_name ?? '';
  const emailValue = email ?? profile.data?.email ?? '';
  const loginValue = login ?? profile.data?.sdu.login ?? '';

  async function submit() {
    if (!firstValue.trim()) { setError('Укажите имя'); return; }
    setError('');
    try {
      await ensureConsents(legal.data, checks, state.legal_required, Boolean(loginValue || password));
      await save.mutateAsync({
        first_name: firstValue.trim(), last_name: lastValue.trim() || null, email: emailValue.trim() || null,
      });
      if (loginValue || password) {
        if (!loginValue.trim() || (!password && !profile.data?.sdu.password_set)) {
          throw new Error('Для СДО/ЛК нужны логин и пароль. Или оставьте оба поля пустыми.');
        }
        await save.mutateAsync({
          sdu_platform: 'РАНХиГС — СДО / ЛК', sdu_login: loginValue.trim(),
          ...(password ? { sdu_password: password } : {}),
        });
        setPassword('');
      }
      await setOnboardingStep('finish');
      await completeOnboarding();
      await queryClient.invalidateQueries({ queryKey: ['first-run', membershipId] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось завершить настройку');
    }
  }
  return (
    <Shell role="Первый запуск · староста" group={state.group_name}>
      <section className="ob-scene">
        <div className="ob-icon">👋</div>
        <h1>Добро пожаловать!</h1>
        <p className="ob-lead">Группа уже подключена. Осталось один раз заполнить ваши личные данные — так же, как у студентов. Настройка старосты в Telegram не изменяется.</p>
        {profile.isPending ? <p className="ob-subtle">Загружаем профиль…</p> : null}
        <div className="ob-form">
          <label>Имя <span className="ob-required">*</span><input type="text" autoComplete="given-name" required maxLength={60}
            value={firstValue} onChange={(e) => setFirst(e.target.value)} placeholder="Имя" /></label>
          <label>Фамилия <small>по желанию</small><input type="text" autoComplete="family-name" maxLength={60}
            value={lastValue} onChange={(e) => setLast(e.target.value)} placeholder="Фамилия" /></label>
          <label>Почта <small>по желанию</small><input type="email" autoComplete="email" maxLength={120}
            value={emailValue} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" /></label>
          <div className="ob-divider">СДО и личный кабинет · необязательно</div>
          <label>Логин СДО/ЛК<input type="text" autoComplete="off" maxLength={80}
            disabled={!legal.data?.ready} value={loginValue} onChange={(e) => setLogin(e.target.value)}
            placeholder="Логин" /></label>
          <label>Пароль СДО/ЛК<input type="password" autoComplete="new-password" maxLength={200}
            disabled={!legal.data?.ready} value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder={profile.data?.sdu.password_set ? 'Сохранён, менять необязательно' : 'Пароль'} /></label>
          <LegalFields status={legal.data} required={state.legal_required} checks={checks} setChecks={setChecks}
            includeSdo={Boolean(loginValue || password)} />
        </div>
        {error || progress.error ? <Notice message={error || progress.error} /> : null}
        <Actions next={() => { void submit(); }} busy={progress.busy || save.isPending} disabled={profile.isPending || legal.isPending || (state.legal_required && !legal.data?.ready)}
          label="Завершить настройку" />
      </section>
    </Shell>
  );
}
