import clsx from 'clsx';
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import type { HomeworkDetail } from '@studgroup/shared-types';
import { isApiRequestError } from '../../api/errors';
import { api, useCompletionMutation, useHomeworkQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { whenLabel } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { photoMaterials, remainingMaterials } from '../../lib/photoMaterials';
import { useProcessingPoll } from '../../lib/processingPoll';
import { subjectStyle } from '../../lib/subjectColor';
import { SubjectMark } from '../../ui/SubjectMark';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { Float } from '../../shell/AppRoot';
import { BackTop } from '../../shell/TopBar';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Banner, Skeleton, Spinner } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';
import { RequestError } from '../../ui/RequestError';
import { DeleteCardButton } from '../shared/DeleteCard';
import { AddFile, Collapsible, SourceRow, UpdatesBlock } from '../shared/CardParts';
import { MaterialList, Photos } from '../shared/Materials';
import { changedAfterCompletion, showUrgent } from '../today/cardStatus';
import { toHomeworkCardViewModel } from './cardViewModel';

/** Personal «Выполнено мной»: set and undo, with conflict and failure handling. */
function CompletionBar({ detail, refreshing, onError }: { detail: HomeworkDetail; refreshing: boolean; onError: (message: string | null) => void }) {
  const { adapter } = useTelegram();
  const mutation = useCompletionMutation(detail.id);
  const done = detail.my_state.completion === 'completed';
  if (!detail.permissions.includes('homework.completion.write')) return null;
  return (
    <Float>
      <div className="cta-bar">
        <button
          type="button"
          className={clsx('btn', done && 'on', mutation.isPending && 'busy')}
          disabled={mutation.isPending || refreshing}
          onClick={() => {
            onError(null);
            mutation.mutate(
              { completed: !done, expectedRevision: detail.revision },
              {
                onSuccess: () => adapter?.haptic('success'),
                onError: (error) => {
                  adapter?.haptic('error');
                  if (isApiRequestError(error) && error.code === 'revision_conflict') onError(ru.detail.completionConflict);
                  else if (isApiRequestError(error) && (error.status === 403 || error.code === 'permission_denied')) onError(ru.detail.completionForbidden);
                  else onError(ru.detail.completionFailed);
                },
              },
            );
          }}
        >
          {mutation.isPending ? <Spinner /> : done ? null : <Ic name="check" size={18} />}
          {done ? ru.detail.unmarkDone : ru.detail.markDone}
        </button>
      </div>
    </Float>
  );
}

function Body({ detail, nowMs, gaveUp, restart, refreshing }: { detail: HomeworkDetail; nowMs: number; gaveUp: boolean; restart: () => void; refreshing: boolean }) {
  const timeZone = useGroupTimeZone();
  const view = toHomeworkCardViewModel(detail);
  const [completionMessage, setCompletionMessage] = useState<string | null>(null);
  const subject = view.subject?.name ?? 'Без предмета';
  const when = whenLabel(detail.deadline, nowMs, timeZone);
  const description = detail.description ?? detail.summary ?? null;
  const photos = photoMaterials(view.materials);
  const editor = detail.permissions.includes('homework.edit');
  const cancelled = detail.status === 'cancelled';
  // Editors only: the list of what changed is a working detail, not news for students.
  const updates = !editor
    ? []
    : view.updates.length
      ? view.updates
      : detail.significant_update
        ? [{ id: 'significant-update', kind: 'change' as const, text: ru.detail.cardUpdated, createdAt: detail.significant_updated_at ?? detail.updated_at }]
        : [];
  const canComplete = !cancelled && view.kind === 'homework';
  return (
    <>
      <BackTop fallback="/tasks" right={editor ? <>{cancelled ? null : <DeleteCardButton kind="homework" id={detail.id} revision={detail.revision} backTo="/tasks" />}<Link className="pillb" to="edit"><Ic name="edit" size={15} />{ru.edit.open}</Link></> : undefined} />
      <div className="d-head" style={subjectStyle(subject)}>
        <span className="chipx"><b><SubjectMark name={subject} /></b>{subject}</span>
        <h1>{detail.title}</h1>
        <div className="m">
          <span className="when">{when.known ? <Ic name="clock" size={14} /> : null}{when.text}</span>
          <span className="chipx ok">{ru.card.kind[view.kind]}</span>
          {showUrgent(detail) ? <span className="chipx warn"><Ic name="bolt" size={12} />срочно</span> : null}
          {cancelled ? <span className="chipx mute">{ru.card.cancelled}</span> : null}
          {changedAfterCompletion(detail) ? <span className="chipx warn"><Ic name="refresh" size={12} />{ru.card.updatedCheck}</span> : null}
        </div>
        <p className="cert">{ru.card.deadlineCertainty[view.deadlineCertainty]}</p>
      </div>
      {detail.processing.state === 'delayed' ? <Banner tone="warning" icon="alert">{ru.card.delayed}</Banner> : null}
      {detail.processing.state === 'reanalyzing' ? <Banner tone="info" icon="refresh">{ru.card.reanalyzing}</Banner> : null}
      {detail.processing.state !== 'none' && gaveUp ? (
        <Banner tone="info" icon="alert" action={<button type="button" className="bact txt" onClick={restart}>{ru.today.recheck}</button>}>{ru.today.pollStopped}</Banner>
      ) : null}
      {detail.deadline.state !== 'known' ? <Banner tone="info" icon="alert">Срок не назван. Мы уточним у старосты и обновим карточку.</Banner> : null}
      {completionMessage ? <Banner tone="danger" icon="alert">{completionMessage}</Banner> : null}
      <div className="block" aria-labelledby="todo-title">
        <h3 id="todo-title">{ru.detail.whatToDo}</h3>
        {photos.length ? <Photos items={photos} alt={detail.title} /> : <p>{description ?? ru.detail.noDescription}</p>}
      </div>
      {editor ? <AddFile fetchLink={() => api.homeworkFileLink(detail.id)} /> : null}
      <UpdatesBlock items={updates} timeZone={timeZone} />
      <Collapsible title={view.sources.length > 1 ? ru.detail.sources : ru.detail.source} label={view.sources.length > 1 ? ru.detail.sources : ru.detail.source}>
        {view.sources.map((source, index) => (
          <SourceRow key={`${source.kind}-${source.message_date ?? 'undated'}-${index}`} source={source} timeZone={timeZone} />
        ))}
      </Collapsible>
      <div className="block">
        <h3 id="homework-materials">Материалы</h3>
        <MaterialList items={remainingMaterials(view.materials)} />
      </div>
      <div className="cta-space" />
      {canComplete ? <CompletionBar detail={detail} refreshing={refreshing} onError={setCompletionMessage} /> : null}
    </>
  );
}

export function HomeworkScreen() {
  const { homeworkId = '' } = useParams();
  const query = useHomeworkQuery(homeworkId);
  const detail = query.data;
  const nowMs = useServerNow(detail?.generated_at, query.dataUpdatedAt);
  const processingActive = detail !== undefined && detail.processing.state !== 'none';
  const { gaveUp, restart } = useProcessingPoll(processingActive, query.refetch);
  if (!detail) {
    return (
      <>
        <BackTop fallback="/tasks" />
        {query.isPending ? <Skeleton label={ru.states.loading} /> : null}
        {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink /> : null}
      </>
    );
  }
  return <Body detail={detail} nowMs={nowMs} gaveUp={gaveUp} restart={restart} refreshing={query.isFetching} />;
}
