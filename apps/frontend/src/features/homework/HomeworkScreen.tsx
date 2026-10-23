import type { HomeworkDetail } from '@studgroup/shared-types';
import clsx from 'clsx';
import { useCallback, useId, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router';
import { api, useHomeworkQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { formatDateTile, formatDeadline, formatShortDate, formatTime } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { useProcessingPoll } from '../../lib/processingPoll';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { useIsHeadman } from '../../session/SessionProvider';
import { useBackButton } from '../../shell/useBackButton';
import { Badge } from '../../ui/Badge';
import { Banner } from '../../ui/Banner';
import { Button } from '../../ui/Button';
import { photoMaterials, remainingMaterials } from '../../lib/photoMaterials';
import { CardPhotos } from '../../ui/CardPhotos';
import { MaterialLinks } from '../../ui/MaterialLinks';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { AlertCircle, ChevronDown, ChevronLeft, Clock, NavTasks, Refresh } from '../../ui/icons';
import {
  changedAfterCompletion,
  needsClarification,
  visibleClarification,
  showUrgent,
} from '../today/cardStatus';
import { processingBadge, urgencyBadge, verificationBadge, sourceLabel } from '../today/homeworkBadges';
import { EditActions } from '../cardEdit/CardEditor';
import { CompletionBar } from './CompletionBar';
import { SourceBlock } from './SourceBlock';
import {
  toHomeworkCardViewModel,
  type HomeworkCardViewModel,
  type LearningCardUpdateView,
} from './cardViewModel';
import styles from './HomeworkScreen.module.css';
import sourceStyles from './SourceBlock.module.css';

function Loading() {
  return (
    <div className={styles.stack} role="status" aria-label={ru.states.loading}>
      <Skeleton height={96} radius={22} />
      <Skeleton height={84} radius={22} />
      <Skeleton height={110} radius={22} />
      <Skeleton height={90} radius={22} />
    </div>
  );
}

interface InfoToggle {
  expanded: boolean;
  panelId: string;
  onToggle: () => void;
}

function Hero({
  detail,
  view,
  nowMs,
  timeZone,
  info,
}: {
  detail: HomeworkDetail;
  view: HomeworkCardViewModel;
  nowMs: number;
  timeZone: string;
  info: InfoToggle | null;
}) {
  const urgent = showUrgent(detail);
  const known = detail.deadline.state === 'known' && detail.deadline.at;
  const tile = known ? formatDateTile(detail.deadline.at as string, timeZone) : null;
  const deadline = formatDeadline(detail.deadline, nowMs, timeZone);
  return (
    <section className={styles.hero}>
      {info ? (
        <button
          type="button"
          className={clsx(styles.infoButton, info.expanded && styles.infoButtonOn)}
          onClick={info.onToggle}
          aria-expanded={info.expanded}
          aria-controls={info.panelId}
          aria-label={ru.detail.info}
        >
          <AlertCircle size={20} />
        </button>
      ) : null}
      <div className={clsx(styles.dateTile, urgent && styles.dateTileUrgent)}>
        {tile ? (
          <>
            <span className={styles.dateDay}>{tile.day}</span>
            <span className={styles.dateMonth}>{tile.month}</span>
          </>
        ) : (
          <NavTasks size={26} />
        )}
      </div>
      <div className={styles.heroText}>
        <h2 className={styles.heroTitle}>{detail.title}</h2>
        <p className={styles.heroDeadline}>
          <Clock size={14} />
          <span>{deadline ? `${ru.detail.deadlinePrefix}: ${deadline}` : ru.card.deadlineAbsent}</span>
        </p>
        <p className={styles.certainty}>{ru.card.deadlineCertainty[view.deadlineCertainty]}</p>
        <div className={styles.heroBadges}>
          <Badge tone="primary">{ru.card.kind[view.kind]}</Badge>
          {detail.status === 'cancelled' ? <Badge tone="neutral">{ru.card.cancelled}</Badge> : null}
          {urgent ? urgencyBadge(detail.urgency) : null}
        </div>
      </div>
    </section>
  );
}

/** Editors only, and collapsed like the sources: the list of what changed is a working detail, not news for students. */
function UpdatesBlock({
  detail,
  items,
  timeZone,
}: {
  detail: HomeworkDetail;
  items: LearningCardUpdateView[];
  timeZone: string;
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (!detail.permissions.includes('homework.edit')) return null;
  if (!items.length && !detail.significant_update) return null;
  const visible = items.length
    ? items
    : [
        {
          id: 'significant-update',
          kind: 'change' as const,
          text: ru.detail.cardUpdated,
          createdAt: detail.significant_updated_at ?? detail.updated_at,
        },
      ];
  return (
    <section className={styles.card} aria-labelledby="updates-title">
      <h2 id="updates-title" className={sourceStyles.title}>
        <button
          type="button"
          className={sourceStyles.toggle}
          aria-expanded={open}
          aria-controls={listId}
          aria-label={`${ru.detail.updates}: ${open ? ru.detail.sourcesHide : ru.detail.sourcesShow}`}
          onClick={() => setOpen((value) => !value)}
        >
          <span>{ru.detail.updates}</span>
          <ChevronDown size={20} className={clsx(sourceStyles.chevron, open && sourceStyles.chevronOpen)} />
        </button>
      </h2>
      <ul id={listId} className={clsx(styles.updates, styles.updatesList)} hidden={!open}>
        {visible.map((item) => (
          <li key={item.id} className={styles.updateItem}>
            <span className={styles.updateKind}>{ru.detail.updateKind[item.kind]}</span>
            <span>{item.text}</span>
            {item.createdAt ? (
              <time dateTime={item.createdAt}>
                {formatShortDate(item.createdAt, timeZone)} · {formatTime(item.createdAt, timeZone)}
              </time>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Clarification is role-aware; cancellation and other warnings remain visible. */
function AlertsBlock({ detail }: { detail: HomeworkDetail }) {
  const isHeadman = useIsHeadman();
  const cancelled = detail.status === 'cancelled';
  const changed = changedAfterCompletion(detail);
  const clarify = visibleClarification(detail, isHeadman);
  const delayed = detail.processing.state === 'delayed';
  if (!cancelled && !changed && !clarify && !delayed) return null;
  return (
    <section className={styles.card}>
      <div className={styles.statusRow}>
        {cancelled ? <Badge tone="neutral">{ru.card.cancelled}</Badge> : null}
        {changed ? (
          <Badge tone="warning" icon={<Refresh />}>
            {ru.card.updatedCheck}
          </Badge>
        ) : null}
        {clarify ? verificationBadge(detail) : null}
        {delayed ? processingBadge(detail.processing.state) : null}
      </div>
    </section>
  );
}

/** What the info button reveals: verification, source and receipt time (no warnings, they are always shown). */
function infoContent(detail: HomeworkDetail, timeZone: string) {
  const verification = needsClarification(detail) ? null : verificationBadge(detail);
  const label = sourceLabel(detail.source);
  const date = detail.source_detail.message_date;
  const processing = detail.processing.state === 'delayed' ? null : processingBadge(detail.processing.state);
  if (!verification && !label && !date && !processing) return null;
  return (
    <>
      <div className={styles.statusRow}>
        {verification}
        {processing}
        {label ? <Badge tone="neutral">{label}</Badge> : null}
      </div>
      {date ? (
        <p className={styles.received}>
          <Clock size={16} />
          <span>
            {ru.detail.received} {formatShortDate(date, timeZone)} · {formatTime(date, timeZone)}
          </span>
        </p>
      ) : null}
    </>
  );
}

/** Smoothly expanding card: the height animates via grid rows, content is inert while collapsed. */
function InfoPanel({ id, expanded, children }: { id: string; expanded: boolean; children: ReactNode }) {
  return (
    <div
      id={id}
      className={clsx(styles.infoWrap, expanded && styles.infoOpen)}
      aria-hidden={!expanded}
      inert={!expanded}
    >
      <div className={styles.infoClip}>
        <section className={clsx(styles.card, styles.infoCard)}>{children}</section>
      </div>
    </div>
  );
}

export function HomeworkScreen() {
  const { homeworkId = '' } = useParams();
  const navigate = useNavigate();
  const query = useHomeworkQuery(homeworkId);
  const detail = query.data;
  const subject = detail ? toHomeworkCardViewModel(detail).subject : null;
  const nowMs = useServerNow(detail?.generated_at, query.dataUpdatedAt);

  const goBack = useCallback(() => {
    if (window.history.state && typeof window.history.state.idx === 'number' && window.history.state.idx > 0)
      void navigate(-1);
    else void navigate('/today', { replace: true });
  }, [navigate]);
  const nativeBack = useBackButton(goBack);

  const processingActive = detail !== undefined && detail.processing.state !== 'none';
  const { gaveUp, restart } = useProcessingPoll(processingActive, query.refetch);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        {!nativeBack ? (
          <button type="button" className={styles.back} onClick={goBack} aria-label={ru.detail.back}>
            <ChevronLeft size={24} />
          </button>
        ) : (
          <span className={styles.backSpacer} />
        )}
        <h1 className={styles.headerTitle}>
          {detail ? (subject?.name ?? 'Без предмета') : <Skeleton height={20} width={160} />}
        </h1>
        <span className={styles.backSpacer} />
      </header>

      {query.isPending ? <Loading /> : null}
      {query.isError && !detail ? (
        <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink />
      ) : null}

      {detail ? (
        <DetailBody
          detail={detail}
          nowMs={nowMs}
          gaveUp={gaveUp}
          restart={restart}
          refreshing={query.isFetching}
        />
      ) : null}
    </div>
  );
}

function DetailBody({
  detail,
  nowMs,
  gaveUp,
  restart,
  refreshing,
}: {
  detail: HomeworkDetail;
  nowMs: number;
  gaveUp: boolean;
  restart: () => void;
  refreshing: boolean;
}) {
  // HomeworkDetail carries no timezone; the group timezone comes from the session.
  const timeZone = useGroupTimeZone();
  const description = detail.description ?? detail.summary ?? null;
  const view = toHomeworkCardViewModel(detail);
  const [infoOpen, setInfoOpen] = useState(false);
  const panelId = useId();
  const info = infoContent(detail, timeZone);
  const photos = photoMaterials(view.materials);
  return (
    <div className={styles.stack}>
      {detail.processing.state === 'delayed' ? (
        <Banner tone="warning" icon={<AlertCircle />}>
          {ru.card.delayed}
        </Banner>
      ) : null}
      {detail.processing.state === 'reanalyzing' ? (
        <Banner tone="info" icon={<Refresh />}>
          {ru.card.reanalyzing}
        </Banner>
      ) : null}
      {detail.processing.state !== 'none' && gaveUp ? (
        <Banner
          tone="info"
          action={
            <Button variant="secondary" onClick={restart}>
              {ru.today.recheck}
            </Button>
          }
        >
          {ru.today.pollStopped}
        </Banner>
      ) : null}

      <Hero
        detail={detail}
        view={view}
        nowMs={nowMs}
        timeZone={timeZone}
        info={info ? { expanded: infoOpen, panelId, onToggle: () => setInfoOpen((v) => !v) } : null}
      />
      {info ? (
        <InfoPanel id={panelId} expanded={infoOpen}>
          {info}
        </InfoPanel>
      ) : null}
      <AlertsBlock detail={detail} />

      <section className={styles.card} aria-labelledby="todo-title">
        <h2 id="todo-title" className={styles.cardTitle}>
          {ru.detail.whatToDo}
        </h2>
        {photos.length ? (
          <CardPhotos items={photos} alt={detail.title} />
        ) : (
          <p className={clsx(styles.description, !description && styles.muted)}>
            {description ?? ru.detail.noDescription}
          </p>
        )}
      </section>

      <EditActions
        allowed={detail.permissions.includes('homework.edit')}
        fetchFileLink={() => api.homeworkFileLink(detail.id)}
      />
      <UpdatesBlock detail={detail} items={view.updates} timeZone={timeZone} />
      <SourceBlock sources={view.sources} timeZone={timeZone} />
      <section className={styles.card} aria-labelledby="homework-materials">
        <h2 id="homework-materials" className={styles.cardTitle}>
          Материалы
        </h2>
        <MaterialLinks items={remainingMaterials(view.materials)} />
      </section>
      <div className={styles.ctaSpace} />
      <CompletionBar detail={detail} refreshing={refreshing} />
    </div>
  );
}
