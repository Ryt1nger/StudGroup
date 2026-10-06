import '@fontsource-variable/inter/wght.css';
import './styles/global.css';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { configureApiClient } from './api/client';
import { loadTelegramAdapter } from './telegram/loadAdapter';

async function start() {
  const isDemo = import.meta.env.MODE === 'demo';
  if (import.meta.env.DEV && import.meta.env.VITE_LOCAL_PREVIEW === 'true' && 'serviceWorker' in navigator) {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.filter((registration) => registration.active?.scriptURL.endsWith('/mockServiceWorker.js')).map((registration) => registration.unregister()));
  }
  const apiBase = import.meta.env.VITE_API_BASE_URL;
  let demoFetch: typeof fetch | undefined;
  // The public demo uses an in-memory Fetch implementation: Telegram WebViews do not need
  // Service Worker support. Local development keeps MSW and the production build keeps neither.
  if (isDemo) {
    const { createStaticDemoFetch } = await import('./mocks/staticDemoFetch');
    demoFetch = createStaticDemoFetch();
  } else if (import.meta.env.DEV && import.meta.env.VITE_API_MOCK === 'true') {
    const { startApiMock } = await import('./mocks/start');
    await startApiMock(apiBase ?? 'https://api.studgroup.example/v1');
  }
  configureApiClient(demoFetch);
  const adapter = await loadTelegramAdapter();
  createRoot(document.getElementById('root')!).render(<App adapter={adapter} />);
}

void start();
