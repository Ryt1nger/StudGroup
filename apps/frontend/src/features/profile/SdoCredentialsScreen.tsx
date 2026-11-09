import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { useProfileQuery, useUpdateProfile } from '../../api/queries';
import { acceptLegal, legalDocument, legalStatus, revokeSdoConsent, type LegalStatus } from '../../api/legal';

const LEGAL_LINKS = [
  ['privacy', 'Политика обработки персональных данных'],
  ['terms', 'Условия использования'],
  ['service', 'Согласие на обработку данных'],
  ['sdo', 'Отдельное согласие на СДО/ЛК'],
  ['cookies', 'Уведомление о технических данных'],
] as const;

/** Public documents can be read without Telegram login or an app session. */
export function LegalDocumentScreen() {
  const { document } = useParams();
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void legalDocument(document ?? '')
      .then((value) => { if (active) setText(value); })
      .catch(() => { if (active) setError('Не удалось загрузить документ'); });
    return () => { active = false; };
  }, [document]);

  return (
    <main style={{ maxWidth: 740, margin: '0 auto', padding: '24px 18px 80px', lineHeight: 1.65 }}>
      <h1 style={{ fontSize: 22 }}>Правовые документы StudGroup</h1>
      <p>Документы доступны для прочтения без регистрации.</p>
      <nav aria-label="Правовые документы" style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {LEGAL_LINKS.map(([slug, title]) => <Link key={slug} to={`/legal/${slug}`}>{title}</Link>)}
      </nav>
      {error ? <p role="alert">{error}</p> : null}
      {!error && !text ? <p>Загрузка…</p> : null}
      {text ? <article style={{ whiteSpace: 'pre-wrap', marginTop: 24 }}>{text}</article> : null}
      <p><Link to="/profile/sdo">Вернуться к настройкам СДО/ЛК</Link></p>
    </main>
  );
}

export function SdoCredentialsScreen() {
  const navigate = useNavigate();
  const profileQuery = useProfileQuery();
  const update = useUpdateProfile();
  const [status, setStatus] = useState<LegalStatus | null>(null);
  const [login, setLogin] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [accepted, setAccepted] = useState({ terms: false, service: false, sdo: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const profile = profileQuery.data;

  useEffect(() => {
    let active = true;
    void legalStatus()
      .then((value) => { if (active) setStatus(value); })
      .catch(() => { if (active) setError('Не удалось загрузить состояние согласий'); });
    return () => { active = false; };
  }, []);

  async function submit() {
    if (!status || !status.ready || busy) return;
    const actualLogin = (login ?? profile?.sdu.login ?? '').trim();
    if (!actualLogin) { setError('Введите логин СДО/ЛК'); return; }
    if (!password && !profile?.sdu.password_set) { setError('Введите пароль СДО/ЛК'); return; }
    if ((!status.terms && !accepted.terms) ||
        (!status.service && !accepted.service) ||
        (!status.sdo && !accepted.sdo)) {
      setError('Подтвердите каждый документ отдельной отметкой'); return;
    }
    setBusy(true);
    setError('');
    try {
      if (!status.terms) await acceptLegal('terms', status.version);
      if (!status.service) await acceptLegal('service', status.version);
      if (!status.sdo) await acceptLegal('sdo', status.version);
      await update.mutateAsync({
        sdu_platform: 'РАНХиГС — СДО / ЛК',
        sdu_login: actualLogin,
        ...(password ? { sdu_password: password } : {}),
      });
      setPassword('');
      setAccepted({ terms: false, service: false, sdo: false });
      setStatus(await legalStatus());
      setSuccess('Данные сохранены. Пароль хранится зашифрованным. Подключение к системам вуза ещё не подтверждено.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить учётные данные');
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await revokeSdoConsent();
      setLogin('');
      setPassword('');
      setStatus(await legalStatus());
      await profileQuery.refetch();
      setSuccess('Согласие отозвано, сохранённые логин и пароль удалены.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось отозвать согласие');
    } finally {
      setBusy(false);
    }
  }

  const missing = [
    !status?.terms && 'terms',
    !status?.service && 'service',
    !status?.sdo && 'sdo',
  ].filter(Boolean) as ('terms' | 'service' | 'sdo')[];

  return (
    <main style={{ maxWidth: 600, margin: '0 auto', padding: '24px 18px 88px' }}>
      <button type="button" onClick={() => void navigate('/profile')}>‹ Назад в профиль</button>
      <h1>Подключение СДО и ЛК</h1>
      <p>В РАНХиГС для СДО и личного кабинета используется одна пара логин/пароль. Заполните их один раз. Этот шаг добровольный — без него доступны обычные функции StudGroup.</p>
      <p>Пароль вводится только здесь, не в Telegram-переписке. StudGroup не сообщает, что вход в СДО уже выполнен.</p>
      {profileQuery.isPending || !status ? <p>Загрузка настроек…</p> : null}
      {status && !status.ready ? <p role="alert">Сохранение пока недоступно: оператор ещё не утвердил документы и реквизиты в настройках сервера.</p> : null}
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }} autoComplete="off">
        <label htmlFor="study-login">Логин СДО / ЛК</label>
        <input id="study-login" name="study-login" type="text" autoComplete="off" maxLength={80}
          value={login ?? profile?.sdu.login ?? ''} onChange={(event) => setLogin(event.target.value)}
          disabled={!status?.ready || profileQuery.isPending}
          placeholder="Ваш логин" style={{ display: 'block', width: '100%', margin: '8px 0 16px' }} />
        <label htmlFor="study-password">Пароль СДО / ЛК</label>
        <input id="study-password" name="study-password" type="password" autoComplete="new-password" maxLength={200}
          value={password} onChange={(event) => setPassword(event.target.value)}
          disabled={!status?.ready || profileQuery.isPending}
          placeholder={profile?.sdu.password_set ? 'Пароль уже сохранён. Введите новый для замены' : 'Ваш пароль'}
          style={{ display: 'block', width: '100%', margin: '8px 0 22px' }} />
        <p>Ознакомьтесь с документами. Для каждого ещё не подтверждённого документа нужна отдельная отметка.</p>
        {missing.map((purpose) => {
          const label = purpose === 'terms' ? 'Принимаю условия использования' :
            purpose === 'service' ? 'Даю согласие на обработку моих данных для работы StudGroup' :
            'Добровольно разрешаю хранить мой логин и пароль СДО/ЛК';
          return (
            <div key={purpose} style={{ marginBottom: 16 }}>
              <label>
                <input type="checkbox" checked={accepted[purpose]} disabled={!status?.ready} onChange={(e) =>
                  setAccepted((old) => ({ ...old, [purpose]: e.target.checked }))} />
                {' '}{label}
              </label>
              {' · '}<Link to={`/legal/${purpose}`}>Прочитать полный текст</Link>
            </div>
          );
        })}
        <p><Link to="/legal/privacy">Политика обработки данных</Link> · <Link to="/legal/cookies">Технические данные</Link></p>
        {error ? <p role="alert">{error}</p> : null}
        {success ? <p role="status">{success}</p> : null}
        <button type="submit" className="btn" disabled={busy || !status?.ready || profileQuery.isPending}>
          {busy ? 'Сохраняем…' : 'Сохранить данные СДО / ЛК'}
        </button>
      </form>
      {profile?.sdu.password_set || status?.sdo ? (
        <p style={{ marginTop: 20 }}>
          <button type="button" disabled={busy} onClick={() => void revoke()}>
            Отозвать согласие и удалить логин и пароль
          </button>
        </p>
      ) : null}
      <p style={{ marginTop: 24 }}>
        <button type="button" onClick={() => void navigate('/profile')}>Пропустить — продолжить без СДО/ЛК</button>
      </p>
    </main>
  );
}
