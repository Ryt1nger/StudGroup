import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router';
import { useProfileQuery } from '../../api/queries';
import { claimSdoLaunchPrompt } from '../../api/sdoLaunchPrompt';
import { useSession } from '../../session/SessionProvider';
import { Ic } from '../../ui/Ic';

/** First-visit launch announcement, never an access gate or consent. */
export function SdoLaunchAnnouncement({ pathname }: { pathname: string }) {
  const { state } = useSession();
  const navigate = useNavigate();
  const onHome = pathname === '/today';
  const hasSession = state.status === 'ready' && state.session.membership !== null;
  const profile = useProfileQuery({ enabled: hasSession && onHome });
  const claimedFor = useRef<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  const userId = state.status === 'ready' ? state.session.user.id : null;

  useEffect(() => {
    if (!onHome || !hasSession || !userId || !profile.data) return;
    if (claimedFor.current === userId) return;
    claimedFor.current = userId;
    if (profile.data.sdu.password_set) return;

    // The backend atomically marks the feature announcement seen for the
    // authenticated Telegram user, including across devices.
    void claimSdoLaunchPrompt()
      .then((show) => setVisible(show))
      .catch(() => {
        // A notification must never block the main screen or flash on
        // failed/expired profile or authentication requests.
      });
  }, [onHome, hasSession, userId, profile.data]);

  useEffect(() => {
    if (visible) closeRef.current?.focus();
  }, [visible]);

  if (!visible) return null;

  const close = () => setVisible(false);
  const connect = () => {
    close();
    void navigate('/profile/sdo');
  };
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
    if (event.key !== 'Tab') return;
    const controls = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button')];
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first && last) {
      last.focus();
      event.preventDefault();
    } else if (!event.shiftKey && document.activeElement === last && first) {
      first.focus();
      event.preventDefault();
    }
  };

  return (
    <div className="sdo-launch-backdrop">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="sdo-launch-title"
        aria-describedby="sdo-launch-description"
        className="sdo-launch-dialog"
        onKeyDown={onKeyDown}
      >
        <button
          ref={closeRef}
          type="button"
          className="sdo-launch-close"
          aria-label="Закрыть объявление"
          onClick={close}
        >
          <span aria-hidden="true">×</span>
        </button>
        <div className="sdo-launch-art" aria-hidden="true">
          <span className="sdo-launch-art-calendar"><Ic name="cal" size={42} /></span>
          <span className="sdo-launch-art-shield"><Ic name="shield" size={22} /></span>
        </div>
        <div className="sdo-launch-eyebrow">НОВАЯ ВОЗМОЖНОСТЬ</div>
        <h2 id="sdo-launch-title">Расписание и СДО — в StudGroup</h2>
        <p id="sdo-launch-description" className="sdo-launch-description">
          Подключите личный кабинет РАНХиГС, чтобы мы могли обновлять ваше
          расписание и находить ссылки на занятия в СДО. Данные проверяются
          автоматически после подключения.
        </p>
        <div className="sdo-launch-privacy">
          <Ic name="lock" size={18} />
          <span>Подключение добровольное. Данные защищены.</span>
        </div>
        <button className="sdo-launch-primary" type="button" onClick={connect}>
          Подключить СДО / ЛК
        </button>
        <button className="sdo-launch-later" type="button" onClick={close}>Позже</button>
      </section>
    </div>
  );
}
