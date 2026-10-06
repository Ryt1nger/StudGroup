import { useSyncExternalStore } from 'react';

/** Demo-only (mock mode) notification state. In memory: nothing is stored or sent anywhere. */
export interface DemoNotification {
  id: string;
  title: string;
  body: string;
  when: string;
  unread: boolean;
}

let items: DemoNotification[] = [
  { id: 'n1', title: 'Срок задания изменился', body: 'Демонстрационный пример уведомления о переносе срока.', when: '5 мин назад', unread: true },
  { id: 'n2', title: 'Новое расписание на неделю', body: 'Демонстрационный пример уведомления о расписании.', when: '1 ч назад', unread: true },
  { id: 'n3', title: 'Задание отмечено выполненным', body: 'Демонстрационный пример: уже прочитанное уведомление.', when: 'Вчера', unread: false },
];
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function useDemoNotifications(): DemoNotification[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => items,
  );
}

export function markDemoRead(id: string): void {
  items = items.map((n) => (n.id === id ? { ...n, unread: false } : n));
  emit();
}

export function markAllDemoRead(): void {
  items = items.map((n) => ({ ...n, unread: false }));
  emit();
}

/** Test helper. */
export function resetDemoNotifications(): void {
  items = items.map((n, i) => ({ ...n, unread: i < 2 }));
  emit();
}
