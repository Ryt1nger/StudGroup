import type { UserProfile } from '@studgroup/shared-types';

/** An empty profile with the calm defaults, for the local mock and the public demo. */
export function demoProfile(): UserProfile {
  return {
    telegram_user_id: 0,
    username: null,
    first_name: null,
    last_name: null,
    email: null,
    sdu: { platform: null, url: null, login: null, calendar_url: null, password_set: false },
    settings: {
      enabled: true,
      new_cards: true,
      changes: true,
      deadlines: true,
      deadline_lead: '1d',
      lessons: false,
      lesson_lead_minutes: 15,
      quiet: true,
      quiet_range: '23-08',
    },
  };
}

/** Applies a PATCH body the way the backend does; a password is remembered only as «set». */
export function applyProfilePatch(profile: UserProfile, body: Record<string, unknown>): UserProfile {
  const next: UserProfile = { ...profile, sdu: { ...profile.sdu } };
  const text = (key: string) => (typeof body[key] === 'string' ? (body[key] as string) : null);
  if ('first_name' in body) next.first_name = text('first_name');
  if ('last_name' in body) next.last_name = text('last_name');
  if ('email' in body) next.email = text('email');
  if ('sdu_platform' in body) next.sdu.platform = text('sdu_platform');
  if ('sdu_url' in body) next.sdu.url = text('sdu_url');
  if ('sdu_login' in body) next.sdu.login = text('sdu_login');
  if ('sdu_calendar_url' in body) next.sdu.calendar_url = text('sdu_calendar_url');
  if (body.clear_sdu_password === true) next.sdu.password_set = false;
  else if (typeof body.sdu_password === 'string' && body.sdu_password) next.sdu.password_set = true;
  if (body.settings && typeof body.settings === 'object') next.settings = { ...profile.settings, ...(body.settings as object) };
  return next;
}
