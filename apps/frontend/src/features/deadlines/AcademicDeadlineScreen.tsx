import { Link, useParams } from 'react-router';
import { api, useAcademicDeadlineQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { photoMaterials, remainingMaterials } from '../../lib/photoMaterials';
import { subjectStyle } from '../../lib/subjectColor';
import { SubjectMark } from '../../ui/SubjectMark';
import { BackTop } from '../../shell/TopBar';
import { Banner, Skeleton } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';
import { RequestError } from '../../ui/RequestError';
import { DeleteCardButton } from '../shared/DeleteCard';
import { AddFile, Collapsible, SourceRow, UpdatesBlock } from '../shared/CardParts';
import { EVENT_KIND } from '../shared/Cards';
import { hasVisibleMaterials, MaterialList, Photos } from '../shared/Materials';
import { formatEventDate } from './formatEventDate';

export function AcademicDeadlineScreen() {
  const { deadlineId = '' } = useParams();
  const query = useAcademicDeadlineQuery(deadlineId);
  const data = query.data;
  const item = data?.item;
  if (!item || !data) {
    return (
      <>
        <BackTop fallback="/deadlines" />
        {query.isPending ? <Skeleton label="Загрузка события" /> : null}
        {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink /> : null}
      </>
    );
  }
  const timeZone = data.group_timezone;
  const subject = (Object.prototype.hasOwnProperty.call(item, 'confirmed_subject') ? item.confirmed_subject?.name : item.subject) ?? 'Без предмета';
  const editor = (data.permissions?.includes('homework.edit') ?? false) && item.revision !== undefined;
  const photos = photoMaterials(item.materials);
  const materials = remainingMaterials(item.materials);
  const updates = (item.updates ?? []).filter((u) => u.text.trim()).map((u) => ({ id: u.id, kind: u.kind, text: u.text, createdAt: u.created_at }));
  // Only the headman sees that the card needs a check, as a small mark; students see the hint only
  // when it is their sole date information.
  const needsClarification =
    item.needs_clarification || item.deadline_certainty === 'needs_clarification' || (!item.deadline_at && !item.window_start);
  const showHint = item.date_hint && !item.deadline_at && !item.window_start;
  return (
    <>
      <BackTop fallback="/deadlines" right={editor ? <>{item.revision !== undefined ? <DeleteCardButton kind="event" id={item.id} revision={item.revision} backTo="/deadlines" /> : null}<Link className="pillb" to="edit"><Ic name="edit" size={15} />{ru.edit.open}</Link></> : undefined} />
      <div className="d-head" style={subjectStyle(subject)}>
        <span className="chipx"><b><SubjectMark name={subject} /></b>{subject}</span>
        <h1>{item.title}</h1>
        <div className="m">
          <span className="chipx warn">{EVENT_KIND[item.kind]}</span>
          <span className="when"><Ic name="clock" size={14} />{formatEventDate(item, timeZone)}</span>
          {editor && needsClarification ? <span className="chipx warn">{ru.card.needsClarification}</span> : null}
        </div>
      </div>
      {showHint ? <Banner tone="info" icon="alert">{item.date_hint}</Banner> : null}
      <div className="block">
        <h3>Что нужно сделать</h3>
        {photos.length ? <Photos items={photos} alt={item.title} /> : <p>{item.description}</p>}
      </div>
      {editor ? <AddFile fetchLink={() => api.eventFileLink(item.id)} /> : null}
      {hasVisibleMaterials(materials) ? <div className="block">
        <h3>Материалы</h3>
        <MaterialList items={materials} />
      </div> : null}
      {editor ? <UpdatesBlock items={updates} timeZone={timeZone} /> : null}
      {item.sources?.length ? (
        <Collapsible title={item.sources.length > 1 ? ru.detail.sources : ru.detail.source} label={item.sources.length > 1 ? ru.detail.sources : ru.detail.source}>
          {item.sources.map((source, index) => <SourceRow key={`${source.kind}-${index}`} source={source} timeZone={timeZone} />)}
        </Collapsible>
      ) : null}
    </>
  );
}
