import type { ConfirmedScheduleRow, ReviewSubgroup, ScheduleSuggestion } from '@studgroup/shared-types';
import type { CabinetSuggestion } from './cabinetScheduleApi';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
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
import { weekKeys } from '../shared/WeekStrip';
import {
  useCabinetImage,
  useCabinetScheduleReview,
  useCabinetUpload,
  useConfirmCabinetSchedule,
  useRetryCabinetUpload,
} from './cabinetScheduleApi';
import { useEventCapabilities } from '../events/api';
import { useConfirmImageSchedule, useImageScheduleReview, useReviewScheduleWeek } from './imageReviewApi';

const WEEKDAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const normalize = (name: string) => name.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е');

export function ImageScheduleReviewScreen() {
  const features = useEventCapabilities();
  const editor = useIsEditor();
  const query = useImageScheduleReview();
  const cabinet = useCabinetScheduleReview(editor);
  const [params, setParams] = useSearchParams();
  const [reload, setReload] = useState(0);
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
      <BackTop fallback="/schedule/manage" />
      <h1 className="ptitle">Загрузка расписания</h1>
      {!editor ? (
        <StateView
          icon="lock"
          title="Нет доступа"
          body="Подтверждать расписание может староста или заместитель."
        />
      ) : (
        <>
          <CabinetUploadForm
            onUploaded={async (id) => {
              setParams({ suggestion: id });
              await cabinet.refetch();
              setReload((value) => value + 1);
            }}
          />
          {cabinet.isPending ? <Skeleton label="Загрузка загруженных расписаний" /> : null}
          {cabinet.isError ? <RequestError error={cabinet.error} onRetry={() => void cabinet.refetch()} /> : null}
          {query.isPending && features.schedule_review ? <Skeleton label="Загрузка предложений из Telegram" /> : null}
          {query.isError && features.schedule_review ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
          {!cabinet.isPending && (!features.schedule_review || !query.isPending) && !items.length ? (
            <Empty
              title="Пока нет черновиков"
              body="Загрузите скриншот выше, чтобы DeepSeek подготовил расписание для проверки."
            />
          ) : null}
          {items.length > 1 ? (
            <div className="field">
              <label>
                Предложение
                <select
                  value={selected?.id}
                  onChange={(event) => setParams({ suggestion: event.target.value })}
                >
                  {items.map((item, index) => (
                    <option key={item.id} value={item.id}>
                      {item.state === 'confirmed' ? 'Подтверждено' :
                         item.state === 'failed' ? 'Ошибка' :
                         item.state === 'queued' || item.state === 'processing' ? 'Распознаётся' :
                         'Ожидает проверки'} · {index + 1}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}
          {selected?.cabinet && (
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
  failed_output: 'DeepSeek не завершил формирование ответа.',
  failed_format: 'Ответ DeepSeek не удалось прочитать.',
  failed_context: 'Слишком много информации для распознавания.',
  failed_provider: 'Не удалось получить корректный ответ DeepSeek.',
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
        <p>Можно закрыть приложение. Результат сохранится здесь и появится автоматически,
          когда обработка закончится.</p>
      )}
    </section>
  );
}

function CabinetImagePreview({ blob }: { blob: Blob }) {
  const imageRef = useRef<HTMLImageElement>(null);
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
  return <img ref={imageRef} alt="Загруженный скриншот расписания" style={{ maxWidth: '100%', borderRadius: 12 }} />;
}

function CabinetUploadForm({ onUploaded }: { onUploaded: (id: string) => Promise<void> }) {
  const serverNow = useSessionServerNow();
  const zone = useGroupTimeZone();
  const [file, setFile] = useState<File | null>(null);
  const [weekStart] = useState(() => weekKeys(dayKey(serverNow() ?? Date.now(), zone))[0] ?? '');
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
      <div className="field"><label>Скриншот расписания
        <input type="file" accept="image/png,image/jpeg,image/webp" required
          onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </label></div>
      {upload.isError ? <p role="alert">{upload.error.message}</p> : null}
      <button className="btn on" type="submit" disabled={!file || upload.isPending}>
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

function ScheduleReviewForm({
  suggestion,
  cabinet,
  partialReview,
  sourceBlob,
  currentVersion,
  subgroups,
  reload,
}: {
  suggestion: ScheduleSuggestion;
  cabinet: boolean;
  partialReview: boolean;
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
  const [problem, setProblem] = useState<string | null>(null);
  const stale = version !== initial.schedule_version;
  const conflict = isApiRequestError(mutation.error) && mutation.error.code === 'revision_conflict';
  const confirmationError = mutation.error instanceof Error ? mutation.error.message : null;
  function update(index: number, patch: Partial<ConfirmedScheduleRow>) {
    setRows((values) =>
      values.map((value, i) =>
        i === index
          ? {
              ...value,
              unresolvedSubgroup:
                patch.subject !== undefined && value.row.subgroup
                  ? value.row.subgroup
                  : value.unresolvedSubgroup,
              row: { ...value.row, ...patch },
            }
          : value,
      ),
    );
  }
  function changeMapping(index: number, mapping: string, scope?: 'once' | 'recurring') {
    setRows((values) =>
      values.map((value, i) => (i === index ? { ...value, mapping, scope: scope ?? value.scope } : value)),
    );
  }
  function confirm() {
    if (!rows.length) return setProblem('Нужна хотя бы одна строка расписания');
    if (stale && !acknowledged) return setProblem('Подтвердите повторную проверку действующего расписания');
    if (!official.data || official.data.schedule_version !== version)
      return setProblem('Расписание изменилось. Загрузите актуальную версию предложения');
    if (
      rows.some(
        ({ row }) =>
          !row.subject.trim() ||
          !row.valid_from ||
          !row.valid_until ||
          row.valid_until < row.valid_from ||
          !row.starts ||
          !row.ends ||
          row.ends <= row.starts,
      )
    )
      return setProblem('Проверьте предмет, период действия и время каждой пары');
    if (rows.some(({ row, unresolvedSubgroup }) => unresolvedSubgroup && !row.subgroup_id))
      return setProblem('Сопоставьте распознанную подгруппу или явно выберите общую пару');
    const targets = [];
    for (const [index, value] of rows.entries()) {
      if (!value.mapping) continue;
      const edit = official.data.lesson_edits.find((item) => item.lesson_id === value.mapping);
      if (!edit) return setProblem('Выбранная пара недоступна. Обновите действующее расписание');
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
        corrected_rows: rows.map(({ row }) => ({
          ...row,
          subject: row.subject.trim(),
          // The server derives the stable UUID from the exact confirmed name.
          // Fuzzy matching the subjects list can attach a mismatched UUID.
          subject_id: null,
        })),
        targets,
        ...(stale ? { acknowledge_schedule_change: true } : {}),
      },
      {
        onSuccess: () => {
          // Return to the manager without leaving duplicate manager entries in browser history.
          const historyIndex = (window.history.state as { idx?: number } | null)?.idx ?? 0;
          if (historyIndex > 0) void navigate(-1);
          else void navigate('/schedule/manage', { replace: true });
        },
      },
    );
  }
  return (
    <>
      {partialReview ? (
        <Banner tone="warning" icon="alert">
          DeepSeek распознал только часть расписания или не уверен в его типе.
          Проверьте все строки по скриншоту. Отсутствующие пары автоматически не удаляются.
        </Banner>
      ) : null}
      {cabinet ? (
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
      )}
      {official.data && official.data.schedule_version !== version ? (
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
      {initial.state === 'confirmed' ? (
        <Banner tone="info" icon="check">
          Это предложение уже подтверждено. Повторных изменений не будет.
        </Banner>
      ) : null}
      <p>
        Проверьте источник, предметы, подгруппы, период и сопоставление с действующими парами. Время группы:{' '}
        {timeZone}.
      </p>
      <div className="field">
        <label>
          Начало недели для сопоставления
          <input type="date" value={weekStart} onChange={(event) => setWeekStart(event.target.value)} />
        </label>
      </div>
      {official.isPending && weekStart ? (
        <Skeleton count={1} label="Загрузка действующего расписания" />
      ) : null}
      {official.isError ? (
        <RequestError error={official.error} onRetry={() => void official.refetch()} />
      ) : null}
      {subjects.isError ? (
        <RequestError error={subjects.error} onRetry={() => void subjects.refetch()} />
      ) : null}
      <form
        className="form"
        aria-label="Подтверждение расписания"
        onSubmit={(event) => {
          event.preventDefault();
          confirm();
        }}
      >
        <fieldset className="event-fields" disabled={initial.state === 'confirmed' || mutation.isPending}>
          {rows.map((value, index) => {
            const row = value.row;
            const choices = subgroups.filter(
              (subgroup) => normalize(subgroup.subject) === normalize(row.subject),
            );
            const subgroupValue = row.subgroup_id ?? (value.unresolvedSubgroup ? 'unresolved' : '');
            return (
              <fieldset className="block" key={index}>
                <legend>Пара {index + 1}</legend>
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
                      value={row.starts}
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
                      value={row.ends}
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
                      onChange={(event) => {
                        const subgroup = choices.find((choice) => choice.id === event.target.value);
                        setRows((values) =>
                          values.map((item, i) =>
                            i === index
                              ? {
                                  ...item,
                                  unresolvedSubgroup: null,
                                  row: {
                                    ...item.row,
                                    subgroup_id: subgroup?.id ?? null,
                                    subgroup: subgroup?.name ?? null,
                                  },
                                }
                              : item,
                          ),
                        );
                      }}
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
                  onClick={() => setRows((values) => values.filter((_item, i) => i !== index))}
                >
                  Убрать строку
                </button>
              </fieldset>
            );
          })}
          <datalist id="review-subjects">
            {subjects.data?.items.map((subject) => (
              <option key={subject.id} value={subject.name} />
            ))}
          </datalist>
          {stale ? (
            <label>
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />{' '}
              Действующее расписание изменилось после распознавания. Я заново проверил строки и сопоставления.
            </label>
          ) : null}
          {problem ? <p role="alert">{problem}</p> : null}
          {conflict ? (
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
          {mutation.isError && !conflict ? (
            <p role="alert">{confirmationError ?? 'Не удалось подтвердить расписание.'}</p>
          ) : null}
          {initial.state === 'pending' ? (
            <button
              type="submit"
              className="btn on"
              disabled={
                !(cabinet ? sourceBlob : initial.source_url) ||
                !official.data ||
                official.isError ||
                subjects.isPending ||
                subjects.isError
              }
            >
              Подтвердить расписание
            </button>
          ) : null}
        </fieldset>
      </form>
    </>
  );
}
