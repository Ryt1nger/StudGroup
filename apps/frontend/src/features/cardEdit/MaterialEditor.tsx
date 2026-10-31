import { useRef, useState } from 'react';
import type { MaterialLink } from '@studgroup/shared-types';
import { changeMaterial, type CardKind } from './materialApi';

export type PendingMaterial = { file?: File; title?: string; url?: string };

interface Props {
  kind: CardKind;
  cardId?: string;
  revision?: number;
  initial?: MaterialLink[];
  onRevision: (revision: number) => void;
  onPending: (items: PendingMaterial[]) => void;
  onBusy: (busy: boolean) => void;
}

export function MaterialEditor({
  kind,
  cardId,
  revision,
  initial = [],
  onRevision,
  onPending,
  onBusy,
}: Props) {
  const [items, setItems] = useState(initial);
  const [pending, setPending] = useState<PendingMaterial[]>([]);
  const [title, setTitle] = useState('');
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [replaceId, setReplaceId] = useState<string>();
  const [editingId, setEditingId] = useState<string>();
  const [editTitle, setEditTitle] = useState('');
  const [editUrl, setEditUrl] = useState('');

  function queued(next: PendingMaterial[]) {
    setPending(next);
    onPending(next);
  }
  async function run(
    operation: 'add' | 'update' | 'remove' | 'upload',
    options: { id?: string; title?: string; url?: string; file?: File },
  ) {
    if (!cardId || revision === undefined) return;
    setBusy(true);
    onBusy(true);
    setError('');
    try {
      const result = await changeMaterial(kind, cardId, revision, operation, options);
      setItems(result.materials);
      onRevision(result.revision);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось изменить материал');
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  function chooseFile(file?: File) {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) return setError('Файл должен быть не больше 20 МБ');
    if (cardId) void run('upload', { file, id: replaceId });
    else queued([...pending, { file }]);
    setReplaceId(undefined);
    if (fileInput.current) fileInput.current.value = '';
  }
  function addLink() {
    const cleanTitle = title.trim();
    const cleanUrl = url.trim();
    try {
      const parsed = new URL(cleanUrl);
      if (parsed.protocol !== 'https:' || parsed.username || parsed.password || !cleanTitle)
        throw new Error();
    } catch {
      setError('Укажите название и HTTPS-ссылку');
      return;
    }
    if (cardId) void run('add', { title: cleanTitle, url: cleanUrl });
    else queued([...pending, { title: cleanTitle, url: cleanUrl }]);
    setTitle('');
    setUrl('');
    setError('');
  }
  return (
    <div className="field" aria-label="Материалы">
      <label>Материалы</label>
      {items.map((item) => (
        <div key={item.id}>
          <div className="srow" style={{ gap: 8, flexWrap: 'wrap' }}>
            <span style={{ flex: 1, minWidth: 120 }}>{item.title}</span>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setEditingId(item.id);
                setEditTitle(item.title);
                setEditUrl(item.kind && item.kind !== 'link' ? '' : item.url);
              }}
            >
              Изменить
            </button>
            {item.kind && item.kind !== 'link' ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setReplaceId(item.id);
                  fileInput.current?.click();
                }}
              >
                Заменить
              </button>
            ) : null}
            <button type="button" disabled={busy} onClick={() => void run('remove', { id: item.id })}>
              Удалить
            </button>
          </div>
          {editingId === item.id ? (
            <div className="field">
              <input
                aria-label="Новое название материала"
                value={editTitle}
                maxLength={255}
                onChange={(event) => setEditTitle(event.target.value)}
              />
              {!item.kind || item.kind === 'link' ? (
                <input
                  aria-label="Новая HTTPS-ссылка"
                  type="url"
                  value={editUrl}
                  onChange={(event) => setEditUrl(event.target.value)}
                />
              ) : null}
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  if (!editTitle.trim()) return setError('Название не может быть пустым');
                  if (!item.kind || item.kind === 'link') {
                    try {
                      const parsed = new URL(editUrl);
                      if (parsed.protocol !== 'https:' || parsed.username || parsed.password)
                        throw new Error();
                    } catch {
                      return setError('Укажите HTTPS-ссылку');
                    }
                  }
                  void run('update', {
                    id: item.id,
                    title: editTitle.trim(),
                    ...(!item.kind || item.kind === 'link' ? { url: editUrl } : {}),
                  });
                  setEditingId(undefined);
                }}
              >
                Сохранить материал
              </button>
              <button type="button" onClick={() => setEditingId(undefined)}>
                Отмена
              </button>
            </div>
          ) : null}
        </div>
      ))}
      {pending.map((item, index) => (
        <div className="srow" key={index} style={{ gap: 8 }}>
          <span style={{ flex: 1 }}>{item.file?.name ?? item.title}</span>
          <button type="button" onClick={() => queued(pending.filter((_, at) => at !== index))}>
            Убрать
          </button>
        </div>
      ))}
      <input
        ref={fileInput}
        type="file"
        aria-label="Выбрать фото или файл"
        onChange={(event) => chooseFile(event.target.files?.[0])}
      />
      <input
        type="text"
        value={title}
        maxLength={255}
        placeholder="Название ссылки"
        aria-label="Название ссылки"
        onChange={(event) => setTitle(event.target.value)}
      />
      <input
        type="url"
        value={url}
        placeholder="https://..."
        aria-label="HTTPS-ссылка"
        onChange={(event) => setUrl(event.target.value)}
      />
      <button type="button" disabled={busy} onClick={addLink}>
        Добавить ссылку
      </button>
      {error ? (
        <p className="ferr" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
