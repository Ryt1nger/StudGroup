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
  const features = useEventCapabilities();
  return useQuery({
    ...detailQueryOptions,
    queryKey: ['staff-schedule', start, end],
    queryFn: () => unwrap(getStaffSchedule({ query: { start, end } })),
    enabled: features.schedule_review === true && !!start && !!end,
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
