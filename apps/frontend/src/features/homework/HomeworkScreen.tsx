import type { HomeworkDetail } from '@studgroup/shared-types';
import clsx from 'clsx';
import { useCallback, useId, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router';
import { useHomeworkQuery } from '../../api/queries';
import { assets } from '../../assets';
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
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { ThemedImage } from '../../ui/ThemedImage';
import { AlertCircle, ChevronLeft, Clock, Refresh } from '../../ui/icons';
import { changedAfterCompletion, needsClarification, visibleClarification, showUrgent } from '../today/cardStatus';
import { processingBadge, urgencyBadge, verificationBadge, sourceLabel } from '../today/homeworkBadges';
import { CompletionBar } from './CompletionBar';
import { SourceBlock } from './SourceBlock';
import styles from './HomeworkScreen.module.css';

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

function Hero({ detail, nowMs, timeZone, info }: { detail: HomeworkDetail; nowMs: number; timeZone: string; info: InfoToggle | null }) {
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
          <ThemedImage asset={assets.homeworkTile} className={styles.tileImg} />
        )}
      </div>
      <div className={styles.heroText}>
        <h2 className={styles.heroTitle}>{detail.title}</h2>
        <p className={styles.heroDeadline}>
          <Clock size={14} />
          <span>{deadline ? `${ru.detail.deadlinePrefix}: ${deadline}` : ru.card.deadlineUnknown}</span>
        </p>
        <div className={styles.heroBadges}>
          <Badge tone="primary">{ru.card.homeworkChip}</Badge>
          {urgent ? urgencyBadge(detail.urgency) : null}
        </div>
      </div>
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
        {changed ? <Badge tone="warning" icon={<Refresh />}>{ru.card.updatedCheck}</Badge> : null}
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
    <div id={id} className={clsx(styles.infoWrap, expanded && styles.infoOpen)} aria-hidden={!expanded} inert={!expanded}>
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
  const nowMs = useServerNow(detail?.generated_at, query.dataUpdatedAt);

  const goBack = useCallback(() => {
    if (window.history.state && typeof window.history.state.idx === 'number' && window.history.state.idx > 0) void navigate(-1);
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
        <h1 className={styles.headerTitle}>{detail ? detail.subject.name : <Skeleton height={20} width={160} />}</h1>
        <span className={styles.backSpacer} />
      </header>

      {query.isPending ? <Loading /> : null}
      {query.isError && !detail ? <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink /> : null}

      {detail ? (
        <DetailBody detail={detail} nowMs={nowMs} gaveUp={gaveUp} restart={restart} refreshing={query.isFetching} />
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
  const [infoOpen, setInfoOpen] = useState(false);
  const panelId = useId();
  const info = infoContent(detail, timeZone);
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
        <Banner tone="info" action={<Button variant="secondary" onClick={restart}>{ru.today.recheck}</Button>}>
          {ru.today.pollStopped}
        </Banner>
      ) : null}

      <Hero
        detail={detail}
        nowMs={nowMs}
        timeZone={timeZone}
        info={info ? { expanded: infoOpen, panelId, onToggle: () => setInfoOpen((v) => !v) } : null}
      />
      {info ? <InfoPanel id={panelId} expanded={infoOpen}>{info}</InfoPanel> : null}
      <AlertsBlock detail={detail} />

      <section className={styles.card} aria-labelledby="todo-title">
        <h2 id="todo-title" className={styles.cardTitle}>
          {ru.detail.whatToDo}
        </h2>
        <p className={clsx(styles.description, !description && styles.muted)}>{description ?? ru.detail.noDescription}</p>
      </section>

      <SourceBlock detail={detail} />
      <div className={styles.ctaSpace} />
      <CompletionBar detail={detail} refreshing={refreshing} />
    </div>
  );
}
