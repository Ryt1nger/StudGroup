import type { ReactNode } from 'react';
import { TopBar } from '../../shell/TopBar';
import { ru } from '../../i18n/ru';
import { ScheduleTimeline } from './ScheduleTimeline';
import type { ScheduleScreenView as View } from './viewModel';
import styles from './ScheduleScreenView.module.css';

/**
 * Schedule screen layout from the approved mockup. Elements without a working scenario in
 * Slice 1 — the «⋯» menu and «Загрузить своё расписание» — are intentionally not rendered.
 * Scope tabs and the week selector render only if the data source provides them.
 */
interface Props {
  view: View;
  onScopeChange?: (key: string) => void;
  /** Banner shown above the timeline (e.g. «Расписание уточняется»). */
  notice?: ReactNode;
  /** Empty / loading / error content shown instead of, or below, the days. */
  children?: ReactNode;
}

export function ScheduleScreenView({ view, onScopeChange, notice, children }: Props) {
  return (
    <>
      <TopBar />
      <div className={styles.page}>
        <h1 className={styles.title}>{ru.nav.schedule}</h1>

        {view.scopes ? (
          <div className={styles.tabs} role="tablist" aria-label={ru.nav.schedule}>
            {view.scopes.options.map((option) => (
              <button
                key={option.key}
                type="button"
                role="tab"
                aria-selected={option.key === view.scopes?.active}
                className={option.key === view.scopes?.active ? styles.tabActive : styles.tab}
                onClick={() => onScopeChange?.(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : null}

        {view.weekLabel ? <div className={styles.week}>{view.weekLabel}</div> : null}
        {notice}

        {view.days.map((day) => (
          <ScheduleTimeline key={day.heading} day={day} />
        ))}
        {children}
      </div>
    </>
  );
}
