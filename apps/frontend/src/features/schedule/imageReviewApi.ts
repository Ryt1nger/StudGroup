import {
  confirmImageScheduleSuggestion,
  getStaffSchedule,
  listImageScheduleSuggestions,
  type ConfirmImageScheduleSuggestionData,
} from '@studgroup/shared-types';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { detailQueryOptions } from '../../api/cachePolicy';
import { unwrap } from '../../api/errors';
import { useEventCapabilities } from '../events/api';
import { useIsEditor } from '../../session/SessionProvider';

export function useImageScheduleReview() {
  const features = useEventCapabilities();
  return useQuery({
    ...detailQueryOptions,
    queryKey: ['image-schedule-review'],
    queryFn: () => unwrap(listImageScheduleSuggestions()),
    enabled: features.schedule_review === true,
  });
}
export function useReviewScheduleWeek(start: string, end: string) {
  const editor = useIsEditor();
  return useQuery({
    ...detailQueryOptions,
    queryKey: ['staff-schedule', start, end],
    queryFn: () => unwrap(getStaffSchedule({ query: { start, end } })),
    // The private cabinet upload is independent of the Telegram image-review rollout flag.
    // This staff endpoint still checks membership on every request.
    enabled: editor && !!start && !!end,
  });
}
export function useConfirmImageSchedule(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfirmImageScheduleSuggestionData['body']) =>
      unwrap(confirmImageScheduleSuggestion({ path: { id }, body })),
    onSuccess: async () => {
      await client.invalidateQueries();
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: ['image-schedule-review'] });
    },
  });
}
