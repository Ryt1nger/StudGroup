import clsx from 'clsx';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { useEventCapabilities } from '../features/events/api';
import { useOnline } from '../lib/hooks';
import { Banner } from '../ui/Blocks';
import { FeedbackProvider, useFeedback } from '../ui/Feedback';
import { Ic } from '../ui/Ic';
import type { IconName } from '../ui/iconData';
import { AppRootContext } from './AppRoot';

const NAV: ReadonlyArray<readonly [string, string, IconName]> = [
  ['/today', 'Сегодня', 'today'],
  ['/tasks', 'Задания', 'tasks'],
  ['/schedule', 'Расписание', 'cal'],
  ['/subjects', 'Предметы', 'book'],
];

const ORDER: Record<string, number> = { today: 0, tasks: 1, deadlines: 1.5, schedule: 2, events: 3, subjects: 4 };

/** Which tab a path belongs to; `null` for screens outside the tab bar (profile). */
function sectionOf(pathname: string): string | null {
  const root = pathname.split('/')[1] ?? '';
  if (root === 'homework' || root === 'deadlines') return 'tasks';
  if (root in ORDER) return root;
  return null;
}

/** Slide direction of a screen change: forward into a detail, back out of it, fade between sections. */
function direction(from: string | null, to: string): 'fwd' | 'back' | 'fade' | 'none' {
  if (from === null || from === to) return 'none';
  const root = (path: string) => path.split('/')[1] ?? '';
  const depth = (path: string) => path.split('/').filter(Boolean).length;
  const a = root(from);
  const b = root(to);
  const tasksGroup = (r: string) => r === 'tasks' || r === 'deadlines';
  if (tasksGroup(a) && tasksGroup(b) && depth(from) === 1 && depth(to) === 1) return 'fade';
  if (a === 'profile' && b === 'profile') return depth(to) >= depth(from) ? 'fwd' : 'back';
  const detail = (path: string) => depth(path) > 1 || root(path) === 'profile';
  if (detail(to) && !detail(from)) return 'fwd';
  if (!detail(to) && detail(from)) return 'back';
  if (detail(to) && detail(from)) return depth(to) >= depth(from) ? 'fwd' : 'back';
  const x = ORDER[b];
  const y = ORDER[a];
  if (x === undefined || y === undefined) return 'fade';
  return x > y ? 'fwd' : x < y ? 'back' : 'fade';
}

const STAGGER = '.top,.titles,.ptitle,.pcard,.stats,.stat,.task,.tile,.ev,.sec,.grph,.grp,.block,.hero,.wd,.d-head,.empty,.seg,.datel,.foot,.cta-bar';

function Frame() {
  const { pathname } = useLocation();
  const online = useOnline();
  const scrollRef = useRef<HTMLDivElement>(null);
  // Direction is derived from the previous path while rendering, so the new screen mounts with it.
  const [nav, setNav] = useState<{ path: string; dir: 'fwd' | 'back' | 'fade' | 'none' }>({ path: pathname, dir: 'none' });
  if (nav.path !== pathname) setNav({ path: pathname, dir: direction(nav.path, pathname) });
  const dir = nav.dir;
  const section = sectionOf(pathname);
  const features = useEventCapabilities();
  const items = [...NAV];
  if (features.events || features.event_review) items.splice(3, 0, ['/events', 'События', 'cal']);
  const index = Math.max(0, items.findIndex(([path]) => path === `/${section}`));

  useLayoutEffect(() => {
    const scroll = scrollRef.current;
    if (scroll) scroll.scrollTop = 0;
  }, [pathname]);

  // Stagger: every element that appears gets the next slot, so a screen builds up from top to bottom.
  useEffect(() => {
    const view = scrollRef.current?.querySelector('.view');
    if (!view) return;
    const apply = () => {
      view.querySelectorAll<HTMLElement>(STAGGER).forEach((el, i) => {
        if (!el.style.getPropertyValue('--i')) el.style.setProperty('--i', String(Math.min(i, 12)));
      });
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(view, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [pathname]);

  return (
    <>
      <div ref={scrollRef} className="scroll">
        <div key={pathname} className={clsx('view', dir === 'none' && 'still')} data-dir={dir === 'none' ? 'none' : dir}>
          <Outlet />
        </div>
      </div>
      <div className="nav-scrim" aria-hidden="true" />
      <nav className={clsx('nav', section === null && 'noact', items.length === 5 && 'with-events')} aria-label="Основная навигация" style={{ ['--idx' as string]: index, ['--tabs' as string]: items.length }}>
        <span className="ind" aria-hidden="true" />
        {items.map(([path, label, icon]) => (
          <NavLink key={path} to={path} aria-current={section === path.slice(1) ? 'page' : undefined}>
            <Ic name={icon} />
            {label}
          </NavLink>
        ))}
      </nav>
      {!online ? (
        <div className="offline">
          <Banner tone="warning" icon="wifioff">Нет соединения. Проверьте интернет. Последние загруженные данные показаны ниже, если они есть.</Banner>
        </div>
      ) : null}
    </>
  );
}

export function AppShell() {
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  return (
    <AppRootContext.Provider value={root}>
      <FeedbackProvider>
        <AppFrame setRoot={setRoot} />
      </FeedbackProvider>
    </AppRootContext.Provider>
  );
}

function AppFrame({ setRoot }: { setRoot: (el: HTMLDivElement | null) => void }) {
  const feedback = useFeedback();
  return (
    <div ref={setRoot} className={clsx('app', feedback.hasUndo && 'has-undo')} id="app">
      <Frame />
    </div>
  );
}
