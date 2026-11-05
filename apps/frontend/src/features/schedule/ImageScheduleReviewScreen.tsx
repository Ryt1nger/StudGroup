import type { ConfirmedScheduleRow, ReviewSubgroup, ScheduleSuggestion } from '@studgroup/shared-types';
import type { CabinetSuggestion } from './cabinetScheduleApi';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { detailQueryOptions } from '../../api/cachePolicy';
import { isApiRequestError } from '../../api/errors';
import { api } from '../../api/queries';
import { addDays, dayKey, formatTime } from '../../lib/format';
import { useIsEditor, useSessionServerNow } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { BackTop } from '../../shell/TopBar';
import { Banner, Empty, Skeleton, StateView } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';
import { EventSource } from '../events/EventCard';
import { WeekStrip, weekKeys } from '../shared/WeekStrip';
import { subjectStyle } from '../../lib/subjectColor';
import {
  useCabinetImage,
  useCabinetScheduleReview,
  useCabinetUpload,
  useConfirmCabinetSchedule,
  useRetryCabinetUpload,
} from './cabinetScheduleApi';
import { useEventCapabilities } from '../events/api';
import { useConfirmImageSchedule, useImageScheduleReview, useReviewScheduleWeek } from './imageReviewApi';

function pairCount(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  const noun = mod10 === 1 && mod100 !== 11 ? 'пара'
    : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? 'пары' : 'пар';
  return `${count} ${noun}`;
}

const WEEKDAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const normalize = (name: string) => name.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е');

export function ImageScheduleReviewScreen() {
  const features = useEventCapabilities();
  const editor = useIsEditor();
  const query = useImageScheduleReview();
  const cabinet = useCabinetScheduleReview(editor);
  const [params, setParams] = useSearchParams();
  const [reload, setReload] = useState(0);
  const editIndexParam = params.get('edit');
  const editingPage = editIndexParam !== null && /^\d+$/.test(editIndexParam);
  const items = [
    ...(cabinet.data?.suggestions ?? []).map((item) => ({ ...item, cabinet: true })),
    ...(query.data?.suggestions ?? []).map((item) => ({ ...item, cabinet: false })),
  ];
  const selected =
    items.find((item) => item.id === params.get('suggestion')) ??
    items.find((item) => item.state === 'pending') ??
    items[0];
  const image = useCabinetImage(
    selected?.cabinet && (selected.state === 'pending' || selected.state === 'confirmed')
      ? selected.id : null,
  );

  return (
    <>
      <BackTop fallback={editingPage ? `/schedule/review?suggestion=${encodeURIComponent(params.get('suggestion') ?? '')}` : '/schedule/manage'} />
      <h1 className="ptitle">{editingPage ? 'Редактирование пары' : 'Загрузка расписания'}</h1>
      {!editor ? (
        <StateView
          icon="lock"
          title="Нет доступа"
          body="Подтверждать расписание может староста или заместитель."
        />
      ) : (
        <>
          {!editingPage ? <CabinetUploadForm
            onUploaded={async (id) => {
              setParams({ suggestion: id });
              await cabinet.refetch();
              setReload((value) => value + 1);
            }}
          /> : null}
          {cabinet.isPending ? <Skeleton label="Загрузка загруженных расписаний" /> : null}
          {cabinet.isError ? <RequestError error={cabinet.error} onRetry={() => void cabinet.refetch()} /> : null}
          {query.isPending && features.schedule_review ? <Skeleton label="Загрузка предложений из Telegram" /> : null}
          {query.isError && features.schedule_review ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
          {!editingPage && !cabinet.isPending && (!features.schedule_review || !query.isPending) && !items.length ? (
            <Empty
              title="Пока нет черновиков"
              body="Загрузите скриншот выше, чтобы распознать расписание и проверить черновик."
            />
          ) : null}
          {!editingPage && selected?.cabinet && (
            selected.state === 'queued' || selected.state === 'processing' || selected.state === 'failed'
          ) ? (
            <CabinetProgress suggestion={selected} />
          ) : null}
          {selected && (selected.state === 'pending' || selected.state === 'confirmed') ? (
            <ScheduleReviewForm
              key={`${selected.id}:${reload}`}
              suggestion={selected as ScheduleSuggestion}
              cabinet={selected.cabinet}
              partialReview={
                selected.cabinet && 'progress_stage' in selected &&
                selected.progress_stage === 'ready_partial'
              }
              oneWeekUpload={selected.cabinet && 'duration' in selected && selected.duration === 'one_week'}
              sourceWeekStart={selected.cabinet && 'week_start' in selected ? selected.week_start : null}
              sourceBlob={selected.cabinet ? (image.data ?? null) : null}
              currentVersion={cabinet.data?.current_schedule_version ?? query.data?.current_schedule_version ?? selected.schedule_version}
              subgroups={cabinet.data?.subgroups ?? query.data?.subgroups ?? []}
              reload={async () => {
                await Promise.all([cabinet.refetch(), query.refetch()]);
                setReload((value) => value + 1);
              }}
            />
          ) : null}
        </>
      )}
    </>
  );
}

const PROGRESS_LABELS: Record<string, string> = {
  queued_a: 'Скриншот в очереди',
  reading_a: 'Читаем текст и таблицу',
  queued_b: 'Текст получен, готовимся проверить расписание',
  reading_b: 'Проверяем пары и даты',
  preparing: 'Собираем черновик',
  failed: 'Не удалось завершить распознавание. Причина предыдущего сбоя неизвестна.',
  failed_budget: 'Лимит распознавания исчерпан.',
  failed_window: 'Период доступного распознавания завершился.',
  failed_hold: 'Распознавание временно заблокировано.',
  failed_output: 'Не удалось завершить распознавание.',
  failed_format: 'Не удалось прочитать результат распознавания.',
  failed_context: 'Слишком много информации для распознавания.',
  failed_provider: 'Не удалось получить корректный результат распознавания.',
  failed_no_lessons: 'Не удалось найти читаемые пары на скриншоте.',
  failed_unreadable: 'Не удалось прочитать изображение.',
  failed_input: 'Не удалось обработать содержимое изображения.',
  failed_validation: 'Не удалось проверить распознанные данные.',
  failed_internal: 'Внутренняя ошибка обработки. Скриншот сохранён.',
  failed_interrupted: 'Обработка была прервана. Скриншот сохранён.',
  failed_checkpoint: 'Не удалось восстановить этап обработки.',
  failed_group: 'Настройки группы не позволяют выполнить распознавание.',
};

function CabinetProgress({ suggestion }: {
  suggestion: CabinetSuggestion;
}) {
  const retry = useRetryCabinetUpload(suggestion.id);
  const failed = suggestion.state === 'failed';
  const progress = Math.min(100, Math.max(0, suggestion.progress_percent));
  return (
    <section className="block cabinet-progress" aria-label="Прогресс распознавания">
      <div className="cabinet-progress-heading">
        <strong>{failed ? 'Обработка остановлена' : 'Распознаём расписание'}</strong>
        <b>{progress}%</b>
      </div>
      <div className="cabinet-progress-track" role="progressbar"
        aria-label="Готовность распознавания"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
        <div className="cabinet-progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <p role="status" aria-live="polite">
        {PROGRESS_LABELS[suggestion.progress_stage] ?? 'Подготавливаем расписание'}
      </p>
      {failed ? (
        <>

          <button className="btn" type="button"
            disabled={!suggestion.can_retry || retry.isPending}
            onClick={() => retry.mutate()}>
            {retry.isPending ? 'Перезапускаем…' : 'Перезапустить анализ'}
          </button>
          {!suggestion.can_retry ? <p>Повторный запуск недоступен.</p> : null}
          {retry.isError ? <RequestError error={retry.error} /> : null}
        </>
      ) : (
        <p>Анализируем скриншот…</p>
      )}
    </section>
  );
}

function CabinetImagePreview({ blob }: { blob: Blob }) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const element = imageRef.current;
    if (!element) return;
    const url = URL.createObjectURL(blob);
    element.src = url;
    return () => {
      element.removeAttribute('src');
      URL.revokeObjectURL(url);
    };
  }, [blob]);
  return (
    <div className="review-source">
      <button type="button" className="review-source-toggle" aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}>
        <span>Исходный скриншот</span>
        <span>{expanded ? 'Свернуть' : 'Показать'}</span>
      </button>
      <img ref={imageRef} className={expanded ? 'review-source-img open' : 'review-source-img'}
        alt="Загруженный скриншот расписания" />
    </div>
  );
}

function CabinetUploadForm({ onUploaded }: { onUploaded: (id: string) => Promise<void> }) {
  const serverNow = useSessionServerNow();
  const zone = useGroupTimeZone();
  const [file, setFile] = useState<File | null>(null);
  const [weekStart, setWeekStart] = useState(() => weekKeys(dayKey(serverNow() ?? Date.now(), zone))[0] ?? '');
  // Default to one-week review. Publishing or changing recurring patterns still requires staff confirmation.
  const upload = useCabinetUpload();
  return (
    <form className="form block" aria-label="Загрузка скриншота расписания" onSubmit={(event) => {
      event.preventDefault();
      if (!file) return;
      upload.mutate({ file, weekStart, duration: 'one_week' }, {
        onSuccess: (result) => {
          setFile(null);
          void onUploaded(result.id);
        },
      });
    }}>
      <h2>Загрузить расписание</h2>
      <div className="field review-upload-week">
        <label>Неделя на скриншоте
          <input type="date" required value={weekStart} onChange={(event) => {
            const selected = event.target.value;
            if (selected) setWeekStart(weekKeys(selected)[0] ?? selected);
          }} />
        </label>
        <small>Выберите неделю, к которой относится расписание. По умолчанию выбрана текущая.</small>
      </div>
      <div className="field"><label>Скриншот расписания
        <input type="file" accept="image/png,image/jpeg,image/webp" required
          onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </label></div>
      {upload.isError ? <p role="alert">{upload.error.message}</p> : null}
      <button className="btn on" type="submit" disabled={!file || !weekStart || upload.isPending}>
        {upload.isPending ? 'Отправляем скриншот…' : 'Анализировать'}
      </button>
    </form>
  );
}

type EditableRow = {
  row: ConfirmedScheduleRow;
  mapping: string;
  scope: 'once' | 'recurring';
  unresolvedSubgroup: string | null;
};

const abbreviatedTime = (value: string) => value.slice(0, 5);
const clipped = (value: string | null | undefined) => Boolean(value && (value.includes('...') || value.includes('…')));

// Missing optional details are warnings only, not publication blockers.
function incompleteLesson(value: EditableRow) {
  const { row } = value;
  return !row.subject.trim() || !row.teacher?.trim() || !row.room?.trim()
    || clipped(row.subject) || clipped(row.teacher) || clipped(row.room)
    || !row.starts || !row.ends || !row.valid_from || !row.valid_until
    || Boolean(value.unresolvedSubgroup && !row.subgroup_id);
}

// A usable timetable row needs a known subject, a valid slot, dates and
// correctly scoped subgroups. Do not publish unknown subgroup rows to everyone.
function publishableLesson(value: EditableRow) {
  const { row } = value;
  return Boolean(row.subject.trim() && row.starts && row.ends && row.ends > row.starts
    && row.valid_from && row.valid_until && row.valid_until >= row.valid_from
    && !(value.unresolvedSubgroup && !row.subgroup_id));
}


function ScheduleReviewForm({
  suggestion,
  cabinet,
  partialReview,
  oneWeekUpload,
  sourceWeekStart,
  sourceBlob,
  currentVersion,
  subgroups,
  reload,
}: {
  suggestion: ScheduleSuggestion;
  cabinet: boolean;
  partialReview: boolean;
  oneWeekUpload: boolean;
  sourceWeekStart: string | null;
  sourceBlob: Blob | null;
  currentVersion: number;
  subgroups: ReviewSubgroup[];
  reload: () => Promise<void>;
}) {
  const [initial] = useState(suggestion);
  const [version] = useState(currentVersion);
  const timeZone = useGroupTimeZone();
  const serverNow = useSessionServerNow();
  const [weekStart, setWeekStart] = useState(
    () =>
      sourceWeekStart ??
      suggestion.rows.find((row) => row.valid_from)?.valid_from ??
      dayKey(serverNow() ?? Date.now(), timeZone),
  );
  const official = useReviewScheduleWeek(weekStart, weekStart ? addDays(weekStart, 6) : '');
  const subjects = useQuery({ ...detailQueryOptions, queryKey: ['subjects'], queryFn: api.subjects });
  const telegramMutation = useConfirmImageSchedule(initial.id);
  const cabinetMutation = useConfirmCabinetSchedule(initial.id);
  const mutation = cabinet ? cabinetMutation : telegramMutation;
  const navigate = useNavigate();
  const [rows, setRows] = useState<EditableRow[]>(() =>
    initial.rows.map((row) => ({
      row: { ...row, valid_from: row.valid_from ?? '', valid_until: row.valid_until ?? '' },
      mapping: '',
      scope: 'once',
      unresolvedSubgroup: row.subgroup ?? null,
    })),
  );
  const [acknowledged, setAcknowledged] = useState(false);
  // A full, non-partial weekly upload is explicitly applied as a weekly snapshot.
  // A partial OCR result requires the editor to opt in before replacing existing lessons.
  const [replaceWeek, setReplaceWeek] = useState(cabinet && oneWeekUpload && !partialReview);
  const [acknowledgedMissingLessons, setAcknowledgedMissingLessons] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const reviewDays = weekKeys(weekStart);
  const [selectedDay, setSelectedDay] = useState(() => {
    const first = rows.find((value) => value.row.weekday >= 0 && value.row.weekday < 7);
    return first?.row.weekday ?? 0;
  });
  const [searchParams, setSearchParams] = useSearchParams();
  const editParam = searchParams.get('edit');
  const editingIndex = editParam !== null && /^\d+$/.test(editParam) && Number(editParam) < rows.length
    ? Number(editParam) : null;
  const [draftEdit, setDraftEdit] = useState<{ index: number; value: EditableRow } | null>(null);
  // Query-string navigation keeps the review draft mounted; reset the shared
  // app viewport so the editor actually opens like a separate screen.
  useLayoutEffect(() => {
    const viewport = document.querySelector<HTMLElement>('.scroll');
    if (viewport) viewport.scrollTop = 0;
  }, [editingIndex]);
  function openEdit(index: number) {
    setDraftEdit({ index, value: { ...rows[index]!, row: { ...rows[index]!.row } } });
    const next = new URLSearchParams(searchParams);
    next.set('edit', String(index));
    setSearchParams(next);
  }
  function closeEdit(save: boolean) {
    if (save && editingIndex !== null && draftEdit?.index === editingIndex) {
      setRows((items) => items.map((item, index) => index === editingIndex ? draftEdit.value : item));
    }
    setDraftEdit(null);
    const next = new URLSearchParams(searchParams);
    next.delete('edit');
    setSearchParams(next, { replace: true });
  }
  const markedDays = new Set(
    reviewDays.filter((_day, index) => rows.some((value) => value.row.weekday === index)),
  );
  const visibleRows = rows
    .map((value, index) => ({ value, index }))
    .filter(({ value }) => value.row.weekday === selectedDay)
    .sort((a, b) => a.value.row.starts.localeCompare(b.value.row.starts));
  const incompleteCount = rows.filter(incompleteLesson).length;
  const publishableCount = rows.filter(publishableLesson).length;
  const skippedCount = rows.length - publishableCount;
  const officialActiveCount = official.data?.schedule.lessons.filter((lesson) => lesson.status === 'scheduled').length ?? 0;
  const fewerLessons = replaceWeek && publishableCount < officialActiveCount;

  const stale = version !== initial.schedule_version;
  const conflict = isApiRequestError(mutation.error) && mutation.error.code === 'revision_conflict';
  const rawConfirmationError = mutation.error instanceof Error ? mutation.error.message : null;
  const confirmationError = (
    isApiRequestError(mutation.error) && String(mutation.error.code) === 'schedule_mapping_required'
  ) || rawConfirmationError?.includes('заменяемую пару')
    ? oneWeekUpload
      ? 'Некоторые пары пересекаются с действующим расписанием. Включите «Заменить расписание этой недели» или проверьте каждую замену.'
      : 'Некоторые пары пересекаются с действующим расписанием. Откройте такую пару и укажите, какое занятие она заменяет.'
    : rawConfirmationError;
  function update(index: number, patch: Partial<ConfirmedScheduleRow>) {
    setDraftEdit((previous) => {
      const value = previous?.index === index ? previous.value : rows[index]!;
      return {
        index,
        value: {
          ...value,
          unresolvedSubgroup:
            patch.subject !== undefined && value.row.subgroup
              ? value.row.subgroup : value.unresolvedSubgroup,
          row: { ...value.row, ...patch },
        },
      };
    });
  }
  function changeMapping(index: number, mapping: string, scope?: 'once' | 'recurring') {
    setDraftEdit((previous) => {
      const value = previous?.index === index ? previous.value : rows[index]!;
      return { index, value: { ...value, mapping, scope: scope ?? value.scope } };
    });
  }
  function updateSubgroup(index: number, subgroupId: string, choices: ReviewSubgroup[]) {
    const subgroup = choices.find((choice) => choice.id === subgroupId);
    setDraftEdit((previous) => {
      const value = previous?.index === index ? previous.value : rows[index]!;
      return {
        index,
        value: {
          ...value,
          unresolvedSubgroup: null,
          row: { ...value.row, subgroup_id: subgroup?.id ?? null, subgroup: subgroup?.name ?? null },
        },
      };
    });
  }
  function confirm() {
    if (!rows.length) return setProblem('Добавьте хотя бы одну пару');
    if (stale && !acknowledged) return setProblem('Проверьте изменившееся расписание');
    if (!official.data || official.data.schedule_version !== version)
      return setProblem('Расписание изменилось. Обновите данные');
    const readyRows = rows.filter(publishableLesson);
    if (replaceWeek && skippedCount > 0)
      return setProblem('Проверьте неполные строки перед заменой расписания недели');
    if (fewerLessons && !acknowledgedMissingLessons)
      return setProblem('Подтвердите, что новые данные содержат меньше пар, чем действующее расписание');
    if (!readyRows.length)
      return setProblem('Нет пар с предметом, временем и датами. Исправьте хотя бы одну');
    const targets = [];
    for (const [index, value] of readyRows.entries()) {
      if (replaceWeek || !value.mapping) continue;
      const edit = official.data.lesson_edits.find((item) => item.lesson_id === value.mapping);
      if (!edit) return setProblem('Выбранная пара недоступна. Обновите расписание');
      targets.push({
        row_index: index,
        lesson_id: edit.lesson_id,
        pattern_revision: edit.pattern_revision,
        override_revision: edit.override_revision,
        scope: value.scope,
      });
    }
    setProblem(null);
    mutation.mutate(
      {
        expected_revision: initial.revision,
        expected_schedule_version: version,
        corrected_rows: readyRows.map(({ row }) => ({
          ...row,
          starts: abbreviatedTime(row.starts),
          ends: abbreviatedTime(row.ends),
          subject: row.subject.trim(),
          // The backend derives the subject identity from the confirmed name.
          subject_id: null,
        })),
        targets,
        ...(replaceWeek ? { replace_week: true } : {}),
        ...(stale ? { acknowledge_schedule_change: true } : {}),
      },
      {
        onSuccess: () => {
          void navigate('/schedule/manage', { replace: true });
        },
      },
    );
  }
  return (
    <>
      {editingIndex === null && (partialReview || incompleteCount > 0) && initial.state === 'pending' ? (
        <div className="review-note" role="status">
          <span className="review-note-dot" aria-hidden="true" />
          <span>{incompleteCount > 0
            ? `${incompleteCount} пар требуют проверки — выделены жёлтым`
            : 'Частичное распознавание — проверьте пары'}</span>
        </div>
      ) : null}
      {editingIndex === null ? (cabinet ? (
        sourceBlob ? (
          <CabinetImagePreview blob={sourceBlob} />
        ) : (
          <Banner tone="warning" icon="alert">
            Исходный скриншот не загружен. Подтверждение недоступно.
          </Banner>
        )
      ) : (
        <>
          <EventSource url={initial.source_url} />
          {!initial.source_url ? (
            <Banner tone="warning" icon="alert">
              Источник изменился или недоступен. Подтверждение этого предложения заблокировано.
            </Banner>
          ) : null}
        </>
      )) : null}
      {editingIndex === null && official.data && official.data.schedule_version !== version ? (
        <Banner
          tone="warning"
          icon="alert"
          action={
            <button type="button" className="bact txt" onClick={() => void reload()}>
              Загрузить актуальную версию
            </button>
          }
        >
          Действующее расписание изменилось. Проверьте предложение заново.
        </Banner>
      ) : null}
      {editingIndex === null && initial.state === 'confirmed' ? (
        <Banner tone="info" icon="check">
          Это предложение уже подтверждено. Повторных изменений не будет.
        </Banner>
      ) : null}
      {editingIndex === null ? <div className="field review-week-field">
        <label>
          Неделя
          <input type="date" value={weekStart} disabled={oneWeekUpload} onChange={(event) => setWeekStart(event.target.value)} />
        </label>
      </div> : null}
      {official.isPending && weekStart && editingIndex === null ? (
        <Skeleton count={1} label="Загрузка действующего расписания" />
      ) : null}
      {official.isError && editingIndex === null ? (
        <RequestError error={official.error} onRetry={() => void official.refetch()} />
      ) : null}
      {subjects.isError && editingIndex === null ? (
        <RequestError error={subjects.error} onRetry={() => void subjects.refetch()} />
      ) : null}
      <form
        className={editingIndex === null ? 'form' : 'form review-edit-screen'}
        aria-label={editingIndex === null ? 'Подтверждение расписания' : 'Редактирование распознанной пары'}
        onSubmit={(event) => {
          event.preventDefault();
          confirm();
        }}
      >
        <fieldset className="event-fields" disabled={initial.state === 'confirmed' || mutation.isPending}>
          {editingIndex === null ? (
            <>
              <WeekStrip
                days={reviewDays}
                selected={selectedDay}
                marked={markedDays}
                onSelect={(index) => setSelectedDay(index)}
              />
              <div className="review-day-heading">
                <strong>{WEEKDAYS[selectedDay]}</strong>
                <span>{pairCount(visibleRows.length)}</span>
              </div>
              {visibleRows.length === 0 ? <p className="review-empty">В этот день пар нет</p> : null}
              <div className="review-lesson-list">
                {visibleRows.map(({ value, index }) => {
                  const incomplete = initial.state === 'pending' && incompleteLesson(value);
                  return (
                    <button
                      type="button"
                      className={`task review-lesson${incomplete ? ' is-incomplete' : ''}`}
                      style={subjectStyle(value.row.subject)}
                      key={index}
                      aria-label={`Редактировать пару: ${value.row.subject}`}
                      onClick={() => openEdit(index)}
                    >
                      <span className="bar" aria-hidden="true" />
                      <span className="review-lesson-time">
                        <strong>{abbreviatedTime(value.row.starts)}</strong>
                        <small>{abbreviatedTime(value.row.ends)}</small>
                      </span>
                      <span className="review-lesson-body">
                        <strong className="review-lesson-title">{value.row.subject}</strong>
                        <span className="review-lesson-meta">
                          {[value.row.room, value.row.teacher, value.row.subgroup].filter(Boolean).join(' · ')
                            || 'Без дополнительных сведений'}
                        </span>
                        {incomplete ? (
                          <span className="review-lesson-flag">Неполные данные · Изменить</span>
                        ) : null}
                      </span>
                      <span className="review-lesson-chevron" aria-hidden="true">›</span>
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            <p className="review-edit-intro">Проверьте название, время и дополнительные сведения. Изменения сохранятся в черновике до публикации расписания.</p>
          )}

          {editingIndex !== null ? [draftEdit?.index === editingIndex ? draftEdit.value : rows[editingIndex]!].map((value) => {
            const index = editingIndex;
            const row = value.row;
            const choices = subgroups.filter(
              (subgroup) => normalize(subgroup.subject) === normalize(row.subject),
            );
            const subgroupValue = row.subgroup_id ?? (value.unresolvedSubgroup ? 'unresolved' : '');
            return (
              <fieldset className="block review-edit" key={index}>
                <legend>Редактирование пары</legend>
                <div className="field">
                  <label>
                    Предмет
                    <input
                      list="review-subjects"
                      value={row.subject}
                      required
                      maxLength={255}
                      onChange={(event) =>
                        update(index, { subject: event.target.value, subgroup_id: null, subgroup: null })
                      }
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    День недели
                    <select
                      value={row.weekday}
                      onChange={(event) => update(index, { weekday: Number(event.target.value) })}
                    >
                      {WEEKDAYS.map((name, day) => (
                        <option key={name} value={day}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="field">
                  <label>
                    Начало пары
                    <input
                      type="time"
                      value={abbreviatedTime(row.starts)}
                      required
                      onChange={(event) => update(index, { starts: event.target.value })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Окончание пары
                    <input
                      type="time"
                      value={abbreviatedTime(row.ends)}
                      required
                      onChange={(event) => update(index, { ends: event.target.value })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Действует с
                    <input
                      type="date"
                      value={row.valid_from}
                      required
                      onChange={(event) => update(index, { valid_from: event.target.value })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Действует до
                    <input
                      type="date"
                      value={row.valid_until}
                      required
                      onChange={(event) => update(index, { valid_until: event.target.value })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Чередование недель
                    <select
                      value={row.week ?? 'all'}
                      onChange={(event) =>
                        update(index, { week: event.target.value as ConfirmedScheduleRow['week'] })
                      }
                    >
                      <option value="all">Каждая неделя</option>
                      <option value="first">Первая неделя</option>
                      <option value="second">Вторая неделя</option>
                    </select>
                  </label>
                </div>
                <div className="field">
                  <label>
                    Подгруппа
                    <select
                      value={subgroupValue}
                      onChange={(event) => updateSubgroup(index, event.target.value, choices)}
                    >
                      <option value="">Общая пара</option>
                      {value.unresolvedSubgroup ? (
                        <option value="unresolved">Нужно сопоставить: {value.unresolvedSubgroup}</option>
                      ) : null}
                      {choices.map((choice) => (
                        <option key={choice.id} value={choice.id}>
                          {choice.name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="field">
                  <label>
                    Преподаватель
                    <input
                      value={row.teacher ?? ''}
                      maxLength={255}
                      onChange={(event) => update(index, { teacher: event.target.value || null })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Аудитория
                    <input
                      value={row.room ?? ''}
                      maxLength={255}
                      onChange={(event) => update(index, { room: event.target.value || null })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Заменить существующую пару
                    <select
                      value={value.mapping}
                      onChange={(event) => changeMapping(index, event.target.value)}
                    >
                      <option value="">Новая пара или точное совпадение</option>
                      {official.data?.schedule.lessons.map((lesson) => (
                        <option key={lesson.id} value={lesson.id}>
                          {dayKey(lesson.starts_at, timeZone)} · {formatTime(lesson.starts_at, timeZone)} ·{' '}
                          {lesson.subject} · {lesson.location}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {value.mapping ? (
                  <div className="field">
                    <label>
                      Область изменения
                      <select
                        value={value.scope}
                        onChange={(event) =>
                          changeMapping(index, value.mapping, event.target.value as 'once' | 'recurring')
                        }
                      >
                        <option value="once">Только выбранная дата</option>
                        <option value="recurring">Регулярно в указанном периоде</option>
                      </select>
                    </label>
                  </div>
                ) : null}
                <button
                  type="button"
                  className="pillb"
                  onClick={() => { setRows((values) => values.filter((_item, i) => i !== index)); closeEdit(false); }}
                >
                  Убрать строку
                </button>
                <div className="review-edit-actions">
                  <button type="button" className="btn on" onClick={() => closeEdit(false)}>Отмена</button>
                  <button type="button" className="btn on" onClick={() => { setSelectedDay(row.weekday); closeEdit(true); }}>Сохранить изменения</button>
                </div>
              </fieldset>
            );
          }) : null}
          <datalist id="review-subjects">
            {subjects.data?.items.map((subject) => (
              <option key={subject.id} value={subject.name} />
            ))}
          </datalist>
          {stale && editingIndex === null ? (
            <label>
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />{' '}
              Действующее расписание изменилось после распознавания. Я заново проверил строки и сопоставления.
            </label>
          ) : null}
          {editingIndex === null && problem ? <p className="review-error" role="alert">{problem}</p> : null}
          {conflict && editingIndex === null ? (
            <Banner
              tone="warning"
              icon="alert"
              action={
                <button type="button" className="bact txt" onClick={() => void reload()}>
                  Загрузить актуальную версию
                </button>
              }
            >
              Источник или расписание изменились. Нужна повторная проверка.
            </Banner>
          ) : null}
          {mutation.isError && !conflict && editingIndex === null ? (
            <p className="review-error" role="alert">{confirmationError ?? 'Не удалось подтвердить расписание.'}</p>
          ) : null}
          {oneWeekUpload && initial.state === 'pending' && editingIndex === null ? (
            <div className="review-week-replace">
              <label className="review-replace-option">
                <input type="checkbox" checked={replaceWeek} onChange={(event) => {
                  setReplaceWeek(event.target.checked);
                  setAcknowledgedMissingLessons(false);
                }} />
                <strong>Заменить расписание этой недели</strong>
              </label>
              <div className="review-replace-counts">
                <span>Сейчас <strong>{pairCount(officialActiveCount)}</strong></span>
                <span>После обновления <strong>{pairCount(publishableCount)}</strong></span>
              </div>
              <p>
                При полной замене прежние занятия этой недели будут отменены,
                а подтверждённые пары добавятся в расписание. Другие недели не изменятся.
                Проверьте, что на скриншоте есть все занятия.
              </p>
              {fewerLessons ? (
                <label className="review-replace-option review-replace-risk">
                  <input type="checkbox" checked={acknowledgedMissingLessons}
                    onChange={(event) => setAcknowledgedMissingLessons(event.target.checked)} />
                  <span>Понимаю, что после замены количество занятий на этой неделе уменьшится.</span>
                </label>
              ) : null}
              {partialReview ? <p className="review-partial-warning">
                Распознавание частичное: обязательно проверьте все дни перед полной заменой.
              </p> : null}
            </div>
          ) : null}
          {initial.state === 'pending' && editingIndex === null ? (
            <div className="review-submit">
              {skippedCount > 0 ? (
                <p className="review-publish-hint" role="status">
                  Будет опубликовано {publishableCount} из {rows.length} пар. Остальные не добавятся.
                </p>
              ) : null}
              <button
                type="submit"
                className="btn on"
                disabled={
                  !(cabinet ? sourceBlob : initial.source_url) ||
                  !official.data ||
                  official.isError ||
                  publishableCount === 0 ||
                  mutation.isPending
                }
              >
                {mutation.isPending ? 'Подтверждаем…' : 'Подтвердить расписание'}
              </button>
            </div>
          ) : null}
        </fieldset>
      </form>
    </>
  );
}
