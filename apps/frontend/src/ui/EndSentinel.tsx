import { useEffect, useRef } from 'react';

/** Loads the next page when the end of a list scrolls into view; there is no «load more» button. */
export function EndSentinel({ onReach, busy }: { onReach: () => void; busy: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const reach = useRef(onReach);
  useEffect(() => {
    reach.current = onReach;
  });
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) reach.current();
      },
      { root: node.closest('.scroll'), rootMargin: '240px 0px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [busy]);
  return (
    <div ref={ref} className="sentinel" role={busy ? 'status' : undefined} aria-label={busy ? 'Загружаем…' : undefined}>
      {busy ? <i className="spinner" aria-hidden="true" /> : null}
    </div>
  );
}
