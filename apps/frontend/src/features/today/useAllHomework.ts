import { useQuery } from '@tanstack/react-query';
import type { HomeworkSummary } from '@studgroup/shared-types';
import { api } from '../../api/queries';
import { mergePages } from '../tasks/taskFilters';

/** Complete active list for Today and counters; separate from Tasks' on-demand pagination. */
export function useAllHomework() {
  return useQuery({
    queryKey: ['homework-counts'],
    queryFn: async (): Promise<HomeworkSummary[]> => {
      const pages = [];
      let cursor: string | undefined;
      do {
        const page = await api.listHomework('all', cursor);
        pages.push(page);
        cursor = page.next_cursor ?? undefined;
      } while (cursor);
      return mergePages(pages);
    },
    staleTime: 30_000,
  });
}
