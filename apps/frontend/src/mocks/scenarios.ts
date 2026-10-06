export const SCENARIOS = [
  'populated',
  'single',
  'empty',
  'delayed',
  'updating',
  'no-group',
  'suspended',
  'left',
  'expired',
  'forbidden',
  'service-error',
  'offline',
  'stale',
  'completion-fails',
  'completion-conflict',
  'slow',
  'next-current',
  'next-null',
  'schedule-clarify',
  'schedule-empty',
  'schedule-error',
  'schedule-forbidden',
  'tasks-empty',
  'tasks-error',
  'tasks-many',
  'tasks-cursor-expired',
  'tasks-more-fails',
  'tasks-dup-page',
  'tasks-empty-first-page',
] as const;

export type Scenario = (typeof SCENARIOS)[number];

export function readScenario(search: string): Scenario {
  const value = new URLSearchParams(search).get('scenario');
  return (SCENARIOS as readonly string[]).includes(value ?? '') ? (value as Scenario) : 'populated';
}
