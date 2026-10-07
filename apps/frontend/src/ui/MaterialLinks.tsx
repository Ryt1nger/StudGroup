import type { MaterialLink } from '@studgroup/shared-types';

export function MaterialLinks({ items }: { items?: MaterialLink[] }) {
  if (items === undefined) return <p>Материалы пока не подключены.</p>;
  const valid = items.filter((item) => {
    try { const url = new URL(item.url); return url.protocol === 'https:' && !url.username && !url.password; }
    catch { return false; }
  });
  if (!valid.length) return <p>Материалы пока не добавлены.</p>;
  return <ul>{valid.map((item) => <li key={item.id}><a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a></li>)}</ul>;
}
