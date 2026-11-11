import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { useProfileQuery, useUpdateProfile } from '../../api/queries';
import { acceptLegal, legalDocument, legalStatus, revokeSdoConsent, type LegalStatus } from '../../api/legal';
import { BackTop } from '../../shell/TopBar';
import { Spinner } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';

const LEGAL_LINKS = [
  ['privacy', 'Политика обработки персональных данных'],
  ['terms', 'Условия использования'],
  ['service', 'Согласие на обработку данных'],
  ['sdo', 'Отдельное согласие на СДО/ЛК'],
  ['cookies', 'Уведомление о технических данных'],
] as const;

const CONSENT_LABELS = {
  terms: 'Принимаю условия использования',
  service: 'Согласен на обработку данных в StudGroup',
  sdo: 'Добровольно разрешаю хранить логин и пароль СДО/ЛК',
} as const;

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
    <main className="sdo-legal">
      <h1 className="ptitle">Документы StudGroup</h1>
      <p className="sdo-subtitle">Правила и согласия доступны без регистрации.</p>
      <nav className="sdo-legal-links" aria-label="Правовые документы">
        {LEGAL_LINKS.map(([slug, title]) => <Link key={slug} to={`/legal/${slug}`}>{title}</Link>)}
      </nav>
      {error ? <p role="alert" className="sdo-feedback error">{error}</p> : null}
      {!error && !text ? <p className="sdo-feedback">Загрузка…</p> : null}
      {text ? <article className="sdo-legal-text">{text}</article> : null}
      <Link className="sdo-secondary-link" to="/profile/sdo">Вернуться к подключению СДО/ЛК</Link>
    </main>
  );
}

export function SdoCredentialsScreen() {
  const profileQuery = useProfileQuery();
  const update = useUpdateProfile();
  const [status, setStatus] = useState<LegalStatus | null>(null);
  const [login, setLogin] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [accepted, setAccepted] = useState({ terms: false, service: false, sdo: false });
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
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

  async function refresh() {
    setRefreshing(true);
    setError('');
    try {
      setStatus(await legalStatus());
    } catch {
      setError('Не удалось проверить состояние подключения. Попробуйте ещё раз.');
    } finally {
      setRefreshing(false);
    }
  }

  const missing = (['terms', 'service', 'sdo'] as const).filter((purpose) => !status?.[purpose]);
  const consentsConfirmed = missing.every((purpose) => accepted[purpose]);
  const ready = status?.credentials_ready === true;
  const loading = !status || profileQuery.isPending;

  async function submit() {
    if (!status?.credentials_ready || busy || !consentsConfirmed) return;
    const actualLogin = ((login ?? profile?.sdu.login ?? '').trim().split('@', 1)[0] ?? '').trim();
    if (!actualLogin) { setError('Введите логин СДО/ЛК'); return; }
    if (!password && !profile?.sdu.password_set) { setError('Введите пароль СДО/ЛК'); return; }
    setBusy(true);
    setError('');
    setSuccess('');
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
      setLogin(actualLogin);
      setAccepted({ terms: false, service: false, sdo: false });
      setStatus(await legalStatus());
      setSuccess('Реквизиты сохранены. Подключение будет проверено при первом обходе.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить реквизиты');
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (busy) return;
    setBusy(true);
    setError('');
    setSuccess('');
    try {
      await revokeSdoConsent();
      setLogin('');
      setPassword('');
      setStatus(await legalStatus());
      await profileQuery.refetch();
      setSuccess('Согласие отозвано, логин и пароль удалены.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось удалить реквизиты');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="sdo-screen">
      <BackTop fallback="/profile" />
      <div className="sdo-heading">
        <div className="sdo-heading-icon" aria-hidden="true"><Ic name="shield" size={23} /></div>
        <h1 className="ptitle">Подключить СДО</h1>
        <p className="sdo-subtitle">Расписание и ссылки из личного кабинета РАНХиГС.</p>
      </div>

      {loading ? <div className="sdo-card sdo-loading" role="status"><Spinner /> Проверяем доступность подключения…</div> : null}

      {!loading && !ready ? (
        <section className="sdo-card sdo-unavailable" aria-label="Статус подключения">
          <div className="sdo-status-icon"><Ic name="lock" size={22} /></div>
          <h2>Подключение пока недоступно</h2>
          <p role="alert">
            {!status.ready
              ? 'Сохранение пока недоступно: оператор ещё не утвердил документы и реквизиты.'
              : 'Сохранение пока недоступно: не настроен ключ шифрования или хранилище YDB.'}
          </p>
          <button type="button" className="pillb" onClick={() => void refresh()} disabled={refreshing}>
            {refreshing ? 'Проверяем…' : 'Проверить снова'}
          </button>
          <Link to="/legal/sdo" className="sdo-secondary-link">Условия подключения</Link>
        </section>
      ) : null}

      {!loading && ready ? (
        <form onSubmit={(event) => { event.preventDefault(); void submit(); }} autoComplete="off">
          {profile?.sdu.password_set ? (
            <div className="sdo-saved" role="status"><Ic name="shield" size={18} /> Реквизиты уже сохранены. Можно обновить их ниже.</div>
          ) : null}

          <section className="sdo-card sdo-form-card" aria-label="Реквизиты СДО">
            <div className="sdo-card-title">Данные для входа</div>
            <div className="field">
              <label htmlFor="study-login">Логин СДО / ЛК</label>
              <input id="study-login" name="study-login" type="text" autoComplete="off" maxLength={80}
                value={login ?? profile?.sdu.login ?? ''} onChange={(event) => setLogin(event.target.value)}
                onBlur={() => setLogin((current) => current === null ? null : (current.trim().split('@', 1)[0] ?? '').trim())}
                placeholder="Логин или почта" />
              <p className="sdo-field-hint">Если введёте почту, сохраним только логин до @.</p>
            </div>
            <div className="field">
              <label htmlFor="study-password">Пароль СДО / ЛК</label>
              <div className="sdo-password-wrap">
                <input id="study-password" name="study-password" type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password" maxLength={200}
                  value={password} onChange={(event) => setPassword(event.target.value)}
                  placeholder={profile?.sdu.password_set ? 'Оставьте пустым, если не меняете' : 'Пароль'} />
                <button type="button" aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                  onClick={() => setShowPassword((current) => !current)}>
                  {showPassword ? 'Скрыть' : 'Показать'}
                </button>
              </div>
            </div>
          </section>

          <section className="sdo-card sdo-consents" aria-label="Согласия">
            <div className="sdo-card-title">Согласия</div>
            {missing.length ? (
              <div className="sdo-consent-list">
                {missing.map((purpose) => (
                  <div key={purpose} className="sdo-consent-item">
                    <input type="checkbox" id={`sdo-consent-${purpose}`} checked={accepted[purpose]}
                      onChange={(event) => setAccepted((old) => ({ ...old, [purpose]: event.target.checked }))} />
                    <div>
                      <label htmlFor={`sdo-consent-${purpose}`}>{CONSENT_LABELS[purpose]}</label>
                      <Link to={`/legal/${purpose}`}>Читать документ</Link>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="sdo-consents-ready"><Ic name="shield" size={18} /> Все необходимые согласия подтверждены.</p>
            )}
            <Link to="/legal/privacy" className="sdo-policy-link">Политика обработки данных</Link>
          </section>

          {error ? <p role="alert" className="sdo-feedback error">{error}</p> : null}
          {success ? <p role="status" className="sdo-feedback success">{success}</p> : null}

          <button type="submit" className="btn sdo-submit" disabled={busy || !consentsConfirmed}>
            {busy ? <><Spinner /> Сохраняем…</> : 'Сохранить данные СДО / ЛК'}
          </button>
          <p className="sdo-privacy-note"><Ic name="lock" size={14} /> Пароль зашифрован. Подключение добровольное.</p>
        </form>
      ) : null}

      {!ready && error ? <p role="alert" className="sdo-feedback error">{error}</p> : null}
      {(profile?.sdu.password_set || status?.sdo) && !loading ? (
        <button type="button" className="sdo-revoke" disabled={busy} onClick={() => void revoke()}>
          Отозвать согласие и удалить реквизиты
        </button>
      ) : null}
      {!ready && success ? <p role="status" className="sdo-feedback success">{success}</p> : null}
      <Link to="/profile" className="sdo-secondary-link sdo-skip">Вернуться в профиль</Link>
    </main>
  );
}
