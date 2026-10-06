import type { HomeworkDetail } from '@studgroup/shared-types';
import { isApiRequestError } from '../../api/errors';
import { useCompletionMutation } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Banner } from '../../ui/Banner';
import { Button } from '../../ui/Button';
import { Check } from '../../ui/icons';
import styles from './CompletionBar.module.css';

/** Personal «Выполнено мной»: set and undo, with conflict and failure handling. */
export function CompletionBar({ detail, refreshing }: { detail: HomeworkDetail; refreshing: boolean }) {
  const { adapter } = useTelegram();
  const mutation = useCompletionMutation(detail.id);
  const done = detail.my_state.completion === 'completed';
  const allowed = detail.permissions.includes('homework.completion.write');
  if (!allowed) return null;

  const error = mutation.error;
  let message: string | null = null;
  if (mutation.isError) {
    if (isApiRequestError(error) && error.code === 'revision_conflict') message = ru.detail.completionConflict;
    else if (isApiRequestError(error) && (error.status === 403 || error.code === 'permission_denied')) message = ru.detail.completionForbidden;
    else message = ru.detail.completionFailed;
  }

  return (
    <div className={styles.bar}>
      {message ? (
        <Banner tone="danger" role="alert">
          {message}
        </Banner>
      ) : null}
      <Button
        block
        variant={done ? 'secondary' : 'primary'}
        icon={done ? undefined : <Check size={18} />}
        busy={mutation.isPending}
        // After a conflict the card must be refetched before the action can be repeated.
        disabled={refreshing}
        onClick={() => {
          mutation.mutate(
            { completed: !done, expectedRevision: detail.revision },
            { onSuccess: () => adapter?.haptic('success'), onError: () => adapter?.haptic('error') },
          );
        }}
      >
        {done ? ru.detail.unmarkDone : ru.detail.markDone}
      </Button>
    </div>
  );
}
