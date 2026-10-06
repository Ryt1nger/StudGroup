import logoSymbolLight from './brand/logo-symbol.light.png';
import logoSymbolDark from './brand/logo-symbol.dark.png';
import wordmarkLight from './brand/wordmark.light.png';
import wordmarkDark from './brand/wordmark.dark.png';
import fileDocLight from './files/file-document.light.png';
import fileDocDark from './files/file-document.dark.png';
import emptyTasksLight from './illustrations/empty-no-tasks.light.png';
import emptyTasksDark from './illustrations/empty-no-tasks.dark.png';
import folderLight from './illustrations/folder-large.light.png';
import folderDark from './illustrations/folder-large.dark.png';
import homeActiveLight from './nav/home.active.light.png';
import homeActiveDark from './nav/home.active.dark.png';
import homeInactiveLight from './nav/home.inactive.light.png';
import homeInactiveDark from './nav/home.inactive.dark.png';
import tasksActiveLight from './nav/tasks.active.light.png';
import tasksActiveDark from './nav/tasks.active.dark.png';
import tasksInactiveLight from './nav/tasks.inactive.light.png';
import tasksInactiveDark from './nav/tasks.inactive.dark.png';
import scheduleActiveLight from './nav/schedule.active.light.png';
import scheduleActiveDark from './nav/schedule.active.dark.png';
import scheduleInactiveLight from './nav/schedule.inactive.light.png';
import scheduleInactiveDark from './nav/schedule.inactive.dark.png';
import subjectsActiveLight from './nav/subjects.active.light.png';
import subjectsActiveDark from './nav/subjects.active.dark.png';
import subjectsInactiveLight from './nav/subjects.inactive.light.png';
import subjectsInactiveDark from './nav/subjects.inactive.dark.png';
import tilePdfLight from './visual/tile-pdf.light.png';
import tilePdfDark from './visual/tile-pdf.dark.png';
import tileDocumentLight from './visual/tile-document.light.png';
import tileDocumentDark from './visual/tile-document.dark.png';
import tileVideoLight from './visual/tile-video.light.png';
import tileVideoDark from './visual/tile-video.dark.png';
import tileLinkLight from './visual/tile-link.light.png';
import tileLinkDark from './visual/tile-link.dark.png';
import tileImageLight from './visual/tile-image.light.png';
import tileImageDark from './visual/tile-image.dark.png';
import tileAnnouncementLight from './visual/tile-announcement.light.png';
import tileAnnouncementDark from './visual/tile-announcement.dark.png';
import tileDeadlineLight from './visual/tile-deadline.light.png';
import tileDeadlineDark from './visual/tile-deadline.dark.png';
import tileUpdateLight from './visual/tile-update.light.png';
import tileUpdateDark from './visual/tile-update.dark.png';
import stackCardLight from './visual/stack-card.light.png';
import nextCardDark from './visual/next-card.dark.png';
import nextCardLight from './visual/next-card.light.png';
import subjectMathLight from './visual/subject-math.light.png';
import subjectMathDark from './visual/subject-math.dark.png';
import subjectEconomicsLight from './visual/subject-economics.light.png';
import subjectEconomicsDark from './visual/subject-economics.dark.png';
import subjectHistoryLight from './visual/subject-history.light.png';
import subjectHistoryDark from './visual/subject-history.dark.png';
import subjectEnglishLight from './visual/subject-english.light.png';
import subjectEnglishDark from './visual/subject-english.dark.png';
import subjectMarketingLight from './visual/subject-marketing.light.png';
import subjectMarketingDark from './visual/subject-marketing.dark.png';
import subjectPeLight from './visual/subject-pe.light.png';
import subjectPeDark from './visual/subject-pe.dark.png';
import calendarLight from './visual/illustration-calendar.light.png';
import calendarDark from './visual/illustration-calendar.dark.png';
import tileCompletedLight from './visual/tile-completed.light.png';
import tileCompletedDark from './visual/tile-completed.dark.png';
import tileExamLight from './visual/tile-exam.light.png';
import tileExamDark from './visual/tile-exam.dark.png';
import statusSuccessLight from './visual/status-success.light.png';
import statusSuccessDark from './visual/status-success.dark.png';
import statusInfoLight from './visual/status-info.light.png';
import statusInfoDark from './visual/status-info.dark.png';
import statusRefreshLight from './visual/status-refresh.light.png';
import statusRefreshDark from './visual/status-refresh.dark.png';
import statusEditedLight from './visual/status-edited.light.png';
import statusEditedDark from './visual/status-edited.dark.png';
import statusErrorLight from './visual/status-error.light.png';
import statusErrorDark from './visual/status-error.dark.png';
import statusInboxLight from './visual/status-inbox.light.png';
import statusInboxDark from './visual/status-inbox.dark.png';
import statusBlockedLight from './visual/status-blocked.light.png';
import statusBlockedDark from './visual/status-blocked.dark.png';
import statusPendingLight from './visual/status-pending.light.png';
import statusPendingDark from './visual/status-pending.dark.png';

export interface ThemedAsset {
  light: string;
  dark: string;
}

/** Raster assets cut from the designer's sheets (design/extracted). SVG replacements are requested in DESIGN_HANDOFF. */
export const assets = {
  logoSymbol: { light: logoSymbolLight, dark: logoSymbolDark },
  wordmark: { light: wordmarkLight, dark: wordmarkDark },
  homeworkTile: { light: fileDocLight, dark: fileDocDark },
  emptyTasks: { light: emptyTasksLight, dark: emptyTasksDark },
  folder: { light: folderLight, dark: folderDark },
  /** Glossy status tiles from the designer's sheet (file and status icons). */
  tiles: {
    pdf: { light: tilePdfLight, dark: tilePdfDark },
    document: { light: tileDocumentLight, dark: tileDocumentDark },
    video: { light: tileVideoLight, dark: tileVideoDark },
    link: { light: tileLinkLight, dark: tileLinkDark },
    image: { light: tileImageLight, dark: tileImageDark },
    announcement: { light: tileAnnouncementLight, dark: tileAnnouncementDark },
    deadline: { light: tileDeadlineLight, dark: tileDeadlineDark },
    update: { light: tileUpdateLight, dark: tileUpdateDark },
    completed: { light: tileCompletedLight, dark: tileCompletedDark },
    exam: { light: tileExamLight, dark: tileExamDark },
  },
  /** Subject tiles (designer's sheet). Subject → tile mapping lives in ui/entityIcons.ts. */
  subjects: {
    math: { light: subjectMathLight, dark: subjectMathDark },
    economics: { light: subjectEconomicsLight, dark: subjectEconomicsDark },
    history: { light: subjectHistoryLight, dark: subjectHistoryDark },
    english: { light: subjectEnglishLight, dark: subjectEnglishDark },
    marketing: { light: subjectMarketingLight, dark: subjectMarketingDark },
    pe: { light: subjectPeLight, dark: subjectPeDark },
  },
  calendar: { light: calendarLight, dark: calendarDark },
  /** Status tiles (second designer sheet). NB: light and dark sheets differ in positions 2, 6, 7 — see DESIGN_HANDOFF §10. */
  status: {
    success: { light: statusSuccessLight, dark: statusSuccessDark },
    info: { light: statusInfoLight, dark: statusInfoDark },
    refresh: { light: statusRefreshLight, dark: statusRefreshDark },
    edited: { light: statusEditedLight, dark: statusEditedDark },
    error: { light: statusErrorLight, dark: statusErrorDark },
    inbox: { light: statusInboxLight, dark: statusInboxDark },
    blocked: { light: statusBlockedLight, dark: statusBlockedDark },
    pending: { light: statusPendingLight, dark: statusPendingDark },
  },
  /** Decor for the next-lesson card. */
  nextCard: { light: stackCardLight, dark: nextCardDark },
  nextCardWide: { light: nextCardLight, dark: nextCardDark },
  nav: {
    home: { active: { light: homeActiveLight, dark: homeActiveDark }, inactive: { light: homeInactiveLight, dark: homeInactiveDark } },
    tasks: { active: { light: tasksActiveLight, dark: tasksActiveDark }, inactive: { light: tasksInactiveLight, dark: tasksInactiveDark } },
    schedule: { active: { light: scheduleActiveLight, dark: scheduleActiveDark }, inactive: { light: scheduleInactiveLight, dark: scheduleInactiveDark } },
    subjects: { active: { light: subjectsActiveLight, dark: subjectsActiveDark }, inactive: { light: subjectsInactiveLight, dark: subjectsInactiveDark } },
  },
} satisfies Record<string, unknown>;
