import type { ReactNode } from 'react';

/** Section heading plus its content; the heading is the accessible name of the group. */
export function Sec({ title, children, id }: { title: string; children: ReactNode; id: string }) {
  return (
    <section aria-labelledby={id}>
      <div className="sec"><h2 id={id}>{title}</h2></div>
      {children}
    </section>
  );
}
