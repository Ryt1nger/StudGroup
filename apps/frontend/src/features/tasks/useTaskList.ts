import { keepPreviousData, useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { isApiRequestError } from '../../api/errors';
import { LIST_RESTART_AFTER_MS, listQueryOptions } from '../../api/cachePolicy';
import { queryKeys } from '../../api/keys';
import { api, type HomeworkListFilter } from '../../api/queries';
import { mergePages } from './taskFilters';

/**
 * Tasks list: server-filtered, cursor-paginated (limit 50). Pages are merged by id, never re-filtered.
 * - Filter change restarts the list (the filter is part of the key and its cursors are bound to it).
 * - An expired/foreign cursor (422 invalid_request) restarts the list ONCE; if the restart fails the
 *   error is shown instead of looping.
 * - A "load more" failure keeps every already fetched card.
 */
export function useTaskList(filter: HomeworkListFilter) {
  const queryClient = useQueryClient();
  const key = queryKeys.homeworkListFor(filter);
  const query = useInfiniteQuery({
    queryKey: key,
    queryFn: ({ pageParam }) => api.listHomework(filter, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData, // no skeleton flash / layout jump while another filter loads
    ...listQueryOptions,
  });
  const restarted = useRef(false);

  const restart = useCallback(() => {
    void queryClient.resetQueries({ queryKey: key });
  }, [queryClient, key]);

  // A list left in the cache for a while must not replay its old cursors: restart it on return.
  useEffect(() => {
    const at = queryClient.getQueryState(key)?.dataUpdatedAt ?? 0; // 0: nothing cached for this filter yet
    if (at > 0 && Date.now() - at > LIST_RESTART_AFTER_MS) restart();
    // Only on mount / filter change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && queryClient.getQueryState(key)?.dataUpdatedAt && Date.now() - (queryClient.getQueryState(key)?.dataUpdatedAt ?? 0) > LIST_RESTART_AFTER_MS) restart();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [queryClient, key, restart]);

  const pages = query.data?.pages;
  const items = useMemo(() => (pages ? mergePages(pages) : []), [pages]);
  const last = pages?.[pages.length - 1];

  const loadMore = useCallback(async () => {
    const result = await query.fetchNextPage();
    if (result.isError && isApiRequestError(result.error) && result.error.code === 'invalid_request') {
      if (!restarted.current) {
        restarted.current = true;
        restart();
      }
    }
  }, [query, restart]);

  // The one-shot restart guard re-arms once a list has loaded cleanly again.
  const settled = query.isSuccess && !query.isFetching && !query.isFetchNextPageError;
  useEffect(() => {
    if (settled) restarted.current = false;
  }, [settled]);

  // One empty page is not an empty list while a cursor remains: keep fetching until items or the end.
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = query;
  const emptyWithCursor = pages !== undefined && !query.isPlaceholderData && items.length === 0 && hasNextPage;
  useEffect(() => {
    if (emptyWithCursor && !isFetchingNextPage && !isFetchNextPageError) void fetchNextPage();
  }, [emptyWithCursor, isFetchingNextPage, isFetchNextPageError, fetchNextPage]);

  return {
    items,
    /** Live-clock anchor: latest page's `generated_at` (selection `server_time` is frozen per cursor chain). */
    generatedAt: last?.generated_at,
    timeZone: last?.group_timezone,
    updatedAt: query.dataUpdatedAt,
    isPending: query.isPending,
    /** Showing the previous filter's cards while the new filter loads. */
    isSwitching: query.isPlaceholderData,
    isError: query.isError && !query.data,
    error: query.error,
    refetch: restart,
    hasNextPage,
    isFetchingNextPage,
    loadMoreFailed: isFetchNextPageError,
    loadMore,
  };
}
