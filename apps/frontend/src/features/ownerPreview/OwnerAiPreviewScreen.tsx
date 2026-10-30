import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';
import { ownerRequest } from '../../api/client';
import { BackTop, SectionTop } from '../../shell/TopBar';
import { Banner, Empty, Skeleton } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';
import { SubjectChip } from '../shared/Cards';

type Card = { id: string; title: string; description?: string; subject?: { name?: string } | string; deadline_at?: string | null; deadline_date_only?: string | null; materials?: Array<{ title: string; telegram_link?: string; url?: string; group?: string }>; candidates?: Array<{ title: string }>; needs_attention?: boolean; reasons?: string[]; kind?: string };
type Preview = { wave: string; cards: Card[]; reviews: Array<{ card_id: string; kind: string; comment: string; created_at: string }>; counts: Record<string, number>; read_only: boolean };
const key = (wave: string) => ['owner-ai-preview', wave];

function usePreview(wave: string) {
  return useQuery<Preview>({ queryKey: key(wave), queryFn: () => ownerRequest(`/owner/ai-preview/${encodeURIComponent(wave)}`), enabled: !!wave, retry: false });
}

function PreviewCard({ card, wave, reviews }: { card: Card; wave: string; reviews: Preview['reviews'] }) {
  const subject = typeof card.subject === 'string' ? card.subject : card.subject?.name;
  const due = card.deadline_at ? new Date(card.deadline_at).toLocaleString('ru-RU') : card.deadline_date_only ?? 'Срок не указан';
  return <article className="task" aria-label={`Открыть preview: ${card.title}`}>
    <span className="bar" />
    <div className="tb"><div className="t">{card.title}</div><div className="s">{card.description || 'Описание отсутствует в сохранённом результате'}</div>
      <div className="m">{subject ? <SubjectChip name={subject} /> : null}<span className="when">{due}</span>{card.needs_attention ? <span className="chipx warn">удержано</span> : <span className="chipx ok">preview</span>}</div>
      <p className="s">Материалы: {card.materials?.length ?? 0}{card.candidates?.length ? ` · ещё ${card.candidates.length} требуют проверки` : ''}</p>
      {reviews.filter((review) => review.card_id === card.id).map((review, index) => <p className="s" key={index}>Замечание: {review.comment}</p>)}
      <Link className="pillb" to={`/owner/ai-preview/${encodeURIComponent(wave)}/cards/${encodeURIComponent(card.id)}`}>Открыть полностью</Link>
    </div>
  </article>;
}

export function OwnerAiPreviewScreen() {
  const { waveKey = '' } = useParams(); const query = usePreview(waveKey);
  return <><SectionTop title="AI Preview" />
    <Banner tone="warning" icon="lock">Закрытый просмотр владельца. Карточки не опубликованы и не меняют данные студентов.</Banner>
    {query.isPending ? <Skeleton label="Загрузка сохранённых shadow-результатов" /> : null}
    {query.isError ? <Empty title="Нет сохранённых результатов" body="Для этой волны ещё нет настоящих shadow-результатов. Демонстрационные карточки не показываются." /> : null}
    {query.data && !query.data.cards.length ? <Empty title="Карточек нет" body="Сохранённая волна не сформировала карточек. Это не заменяется примерами." /> : null}
    {query.data?.cards.filter((card) => !card.needs_attention).map((card) => <PreviewCard key={card.id} card={card} wave={waveKey} reviews={query.data!.reviews} />)}
    {query.data?.cards.some((card) => card.needs_attention) ? <><h2 className="stitle">Удержанные карточки</h2>{query.data.cards.filter((card) => card.needs_attention).map((card) => <PreviewCard key={card.id} card={card} wave={waveKey} reviews={query.data!.reviews} />)}</> : null}
  </>;
}

export function OwnerAiPreviewDetailScreen() {
  const { waveKey = '', cardId = '' } = useParams(); const query = usePreview(waveKey); const client = useQueryClient();
  const card = query.data?.cards.find((item) => item.id === cardId);
  const save = useMutation({ mutationFn: (body: { kind: string; comment: string }) => ownerRequest(`/owner/ai-preview/${encodeURIComponent(waveKey)}/reviews`, { method: 'POST', body: JSON.stringify({ card_id: cardId, ...body }) }), onSuccess: () => client.invalidateQueries({ queryKey: key(waveKey) }) });
  if (query.isPending) return <><BackTop fallback={`/owner/ai-preview/${waveKey}`} /><Skeleton label="Загрузка карточки" /></>;
  if (!card) return <><BackTop fallback={`/owner/ai-preview/${waveKey}`} /><Empty title="Карточка не найдена" body="Она отсутствует в сохранённом результате этой волны." /></>;
  return <><BackTop fallback={`/owner/ai-preview/${waveKey}`} /><h1 className="ptitle">{card.title}</h1>
    {card.needs_attention ? <Banner tone="warning" icon="alert">Карточка удержана и не видна студентам.</Banner> : <Banner tone="info" icon="lock">Это изолированный preview, не публикация.</Banner>}
    <div className="block"><h3>Полное описание</h3><p className="event-description">{card.description || 'Описание отсутствует в сохранённом AI-результате.'}</p></div>
    <div className="block"><h3>Срок</h3><p>{card.deadline_at ? new Date(card.deadline_at).toLocaleString('ru-RU') : card.deadline_date_only ?? 'Не указан'}</p></div>
    <div className="block"><h3>Материалы и фотографии</h3>{card.materials?.length ? card.materials.map((item, index) => <p key={index}><a href={item.url || item.telegram_link} target="_blank" rel="noreferrer">{item.title}</a> {item.group ? `(${item.group})` : ''}</p>) : <p>Подтверждённых материалов нет.</p>}</div>
    <ReviewForm busy={save.isPending} onSave={(kind, comment) => save.mutate({ kind, comment })} />
    {save.isError ? <RequestError error={save.error} onRetry={() => undefined} /> : null}
  </>;
}

function ReviewForm({ busy, onSave }: { busy: boolean; onSave: (kind: string, comment: string) => void }) {
  return <form className="block" onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); onSave(String(form.get('kind')), String(form.get('comment'))); event.currentTarget.reset(); }}><h3>Оставить замечание</h3>
    <select name="kind" defaultValue="other"><option value="correct">Всё верно</option><option value="wrong_subject">Неверный предмет</option><option value="wrong_deadline">Неверный срок</option><option value="incomplete_description">Неполное описание</option><option value="extra_material">Лишний материал</option><option value="missing_material">Нет материала</option><option value="false_homework">Это не домашнее задание</option><option value="wrong_merge">Неверно объединено</option><option value="other">Другое</option></select>
    <textarea name="comment" required maxLength={2000} placeholder="Что нужно исправить?" /><button className="btn sbtn on" type="submit" disabled={busy}>Сохранить замечание</button></form>;
}
