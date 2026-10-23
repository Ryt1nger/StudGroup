import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import type { HomeworkSummary } from '@studgroup/shared-types';
import { api, restartHomeworkLists } from '../../api/queries';
import { queryKeys } from '../../api/keys';
import { useFeedback } from '../../ui/Feedback';

/**
 * «Выполнено мной» from a list: the card's own revision goes with the write, a conflict restarts the
 * lists, and a mark set from a list can be undone for five seconds.
 */
export function useToggleDone() {
  const queryClient = useQueryClient();
  const { toast, undo } = useFeedback();
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());

  const settle = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.today });
    void queryClient.invalidateQueries({ queryKey: queryKeys.lessonSubjects });
    restartHomeworkLists(queryClient);
  }, [queryClient]);

  const write = useCallback(
    async (item: Pick<HomeworkSummary, 'id' | 'revision'>, completed: boolean): Promise<boolean> => {
      setBusy((current) => new Set(current).add(item.id));
      try {
        await api.setCompletion(item.id, completed, item.revision);
        settle();
        return true;
      } catch {
        toast('Не удалось сохранить отметку');
        settle();
        return false;
      } finally {
        setBusy((current) => {
          const next = new Set(current);
          next.delete(item.id);
          return next;
        });
      }
    },
    [settle, toast],
  );

  const toggle = useCallback(
    async (item: HomeworkSummary) => {
      const completed = item.my_state.completion !== 'completed';
      if (!(await write(item, completed))) return;
      if (completed) undo('Отмечено выполненным', () => void write(item, false));
      else toast('Отметка снята');
    },
    [toast, undo, write],
  );

  return { toggle, busy };
}
