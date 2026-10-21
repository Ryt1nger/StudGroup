import { useCallback } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { api, useAcademicDeadlineQuery } from '../../api/queries';
import { useIsHeadman } from '../../session/SessionProvider';
import { useBackButton } from '../../shell/useBackButton';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { ChevronLeft, Clock } from '../../ui/icons';
import { formatEventDate } from './formatEventDate';
import styles from './Deadlines.module.css';
import { photoMaterials, remainingMaterials } from '../../lib/photoMaterials';
import { CardPhotos } from '../../ui/CardPhotos';
import { MaterialLinks } from '../../ui/MaterialLinks';
import layout from '../homework/HomeworkScreen.module.css';
import { SourceBlock } from '../homework/SourceBlock';
import { formatShortDate, formatTime } from '../../lib/format';
import { ru } from '../../i18n/ru';
import { EditActions } from '../cardEdit/CardEditor';

export function AcademicDeadlineScreen() {
  const { deadlineId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const hint: unknown = location.state?.backTo;
  const backTo =
    typeof hint === 'string' && /^\/(?:today|deadlines|subjects\/lessons\/[\w:.-]+)(?:\?[^#]*)?$/.test(hint)
      ? hint
      : '/deadlines';
  const back = useCallback(() => {
    void navigate(backTo);
  }, [navigate, backTo]);
  const nativeBack = useBackButton(back);
  const query = useAcademicDeadlineQuery(deadlineId);
  const headman = useIsHeadman();
  const data = query.data;
  const item = data?.item;
  const subject = item
    ? Object.prototype.hasOwnProperty.call(item, 'confirmed_subject')
      ? item.confirmed_subject
      : { id: '', name: item.subject }
    : null;
  const labels = {
    control_point: 'Контрольная точка',
    assessment: 'Контрольная работа',
    test: 'Тест',
    credit: 'Зачёт',
    exam: 'Экзамен',
    other_academic_deadline: 'Учебное событие',
  };
  return (
    <div className={layout.page}>
      <header className={layout.header}>
        {!nativeBack ? (
          <button type="button" className={layout.back} aria-label="Назад" onClick={back}>
            <ChevronLeft size={24} />
          </button>
        ) : (
          <span className={layout.backSpacer} />
        )}
        <h1 className={layout.headerTitle}>
          {item ? (subject?.name ?? 'Без предмета') : <Skeleton height={20} width={160} />}
        </h1>
        <span className={layout.backSpacer} />
      </header>
      {query.isPending ? (
        <div role="status" aria-label="Загрузка события">
          <Skeleton height={44} />
          <Skeleton height={160} />
        </div>
      ) : null}
      {query.isError ? (
        <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink />
      ) : null}
      {item && data ? (
        <div className={layout.stack}>
          <section className={styles.card}>
            <h2 className={styles.detailTask}>{item.title}</h2>
            <p>{labels[item.kind]}</p>
            <p className={styles.when}>
              <Clock size={16} />
              <span>{formatEventDate(item, data.group_timezone)}</span>
            </p>
            {item.deadline_certainty ? <p>{ru.card.deadlineCertainty[item.deadline_certainty]}</p> : null}
          </section>
          <section className={`${styles.card} ${styles.detailBody}`} aria-labelledby="event-task">
            <h2 id="event-task" className={styles.detailTask}>
              Что нужно сделать
            </h2>
            {photoMaterials(item.materials).length ? (
              <CardPhotos items={photoMaterials(item.materials)} alt={item.title} />
            ) : (
              <p>{item.description}</p>
            )}
          </section>
          <EditActions
            allowed={(data.permissions?.includes('homework.edit') ?? false) && item.revision !== undefined}
            fetchFileLink={() => api.eventFileLink(item.id)}
          />
          <section className={styles.card} aria-labelledby="event-materials">
            <h2 id="event-materials" className={styles.detailTask}>
              Материалы
            </h2>
            <MaterialLinks items={remainingMaterials(item.materials)} />
          </section>
          {item.updates?.length ? (
            <section className={styles.card} aria-labelledby="event-updates">
              <h2 id="event-updates" className={styles.detailTask}>
                {ru.detail.updates}
              </h2>
              <ul className={layout.updates}>
                {item.updates.map((update) => (
                  <li key={update.id} className={layout.updateItem}>
                    <span className={layout.updateKind}>{ru.detail.updateKind[update.kind]}</span>
                    <span>{update.text}</span>
                    <time dateTime={update.created_at}>
                      {formatShortDate(update.created_at, data.group_timezone)} ·{' '}
                      {formatTime(update.created_at, data.group_timezone)}
                    </time>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {item.sources?.length ? (
            <SourceBlock sources={item.sources} timeZone={data.group_timezone} />
          ) : null}
          {item.date_hint && (headman || (!item.deadline_at && !item.window_start)) ? (
            <section className={`${styles.card} ${styles.detailBody}`}>
              <p className={styles.warning}>{item.date_hint}</p>
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
