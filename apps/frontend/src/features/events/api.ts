import {
  getPublishedGroupEvent,
  listImageEventDrafts,
  listPublishedGroupEvents,
  reviewImageEvent,
  type ReviewImageEventData,
} from '@studgroup/shared-types';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { detailQueryOptions } from '../../api/cachePolicy';
import { unwrap } from '../../api/errors';
import { useSession } from '../../session/SessionProvider';

export function useEventCapabilities() {
  const { state } = useSession();
  return state.status === 'ready' ? (state.session.features ?? {}) : {};
}

const eventKeys = ['group-events'] as const;
export function useGroupEvents(options: { start?: string; end?: string; history?: boolean } = {}) {
  const features = useEventCapabilities();
  return useQuery({
    ...detailQueryOptions,
    queryKey: [...eventKeys, 'list', options],
    queryFn: () =>
      unwrap(
        listPublishedGroupEvents({
          query: {
            ...(options.start && options.end ? { start: options.start, end: options.end } : {}),
            status: options.history ? 'all' : 'active',
            upcoming: !options.start && !options.history,
          },
        }),
      ),
    enabled: features.events === true && (!options.start || !!options.end),
  });
}

export function useGroupEvent(id: string) {
  const features = useEventCapabilities();
  return useQuery({
    ...detailQueryOptions,
    queryKey: [...eventKeys, 'detail', id],
    queryFn: () => unwrap(getPublishedGroupEvent({ path: { id } })),
    enabled: !!id && features.events === true,
  });
}

export function useEventReview() {
  const features = useEventCapabilities();
  return useQuery({
    ...detailQueryOptions,
    queryKey: [...eventKeys, 'review'],
    queryFn: () => unwrap(listImageEventDrafts()),
    enabled: features.event_review === true,
  });
}

export function useReviewEvent(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ReviewImageEventData['body']) => unwrap(reviewImageEvent({ path: { id }, body })),
    onSuccess: async () => {
      // Every list interval and detail uses this entity; moves cannot leave an old calendar cell.
      await client.invalidateQueries({ queryKey: eventKeys });
    },
    // A revision conflict requires a fresh review, never a blind retry/overwrite.
    onError: () => {
      void client.invalidateQueries({ queryKey: eventKeys });
    },
  });
}
