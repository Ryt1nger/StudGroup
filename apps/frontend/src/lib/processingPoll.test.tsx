import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useProcessingPoll } from './processingPoll';

describe('useProcessingPoll', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('polls after 10 s and then every 30 s while active', async () => {
    const refetch = vi.fn(() => Promise.resolve());
    renderHook(() => useProcessingPoll(true, refetch));
    await act(async () => void (await vi.advanceTimersByTimeAsync(9_999)));
    expect(refetch).not.toHaveBeenCalled();
    await act(async () => void (await vi.advanceTimersByTimeAsync(1)));
    expect(refetch).toHaveBeenCalledTimes(1);
    await act(async () => void (await vi.advanceTimersByTimeAsync(30_000)));
    expect(refetch).toHaveBeenCalledTimes(2);
  });

  it('does not arm a new timer when an in-flight request finishes after the screen was left', async () => {
    let finish: () => void = () => undefined;
    const refetch = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    const { unmount } = renderHook(() => useProcessingPoll(true, refetch));
    await act(async () => void (await vi.advanceTimersByTimeAsync(10_000)));
    expect(refetch).toHaveBeenCalledTimes(1); // request in flight

    unmount(); // user leaves the screen during the request
    finish(); // the request completes after cleanup
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(0);

    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('also stops when polling becomes inactive during a request', async () => {
    let finish: () => void = () => undefined;
    const refetch = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    const { rerender } = renderHook(({ active }) => useProcessingPoll(active, refetch), { initialProps: { active: true } });
    await act(async () => void (await vi.advanceTimersByTimeAsync(10_000)));
    rerender({ active: false });
    finish();
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});
