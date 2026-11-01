import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../utils/api';
import type {
  DayAheadForecastResponse,
  CutoffPolicyResponse,
  JanuaryBenchmarkResponse,
  SystemHealthResponse,
  IngestionCoverageResponse,
  IngestionSyncResponse,
  CutoffTimeUtc,
} from '../types/windApi';

interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  processTimeMs: number;
}

function useApiCall<T>(fetcher: () => Promise<{ data: T; processTimeMs: number }>, deps: unknown[]) {
  const [state, setState] = useState<ApiState<T>>({ data: null, loading: true, error: null, processTimeMs: 0 });

  const refetch = useCallback(() => {
    setState(s => ({ ...s, loading: true, error: null }));
    fetcher()
      .then(({ data, processTimeMs }) => setState({ data, loading: false, error: null, processTimeMs }))
      .catch((e: Error) => setState(s => ({ ...s, loading: false, error: e.message })));
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { refetch(); }, [refetch]);

  return { ...state, refetch };
}

export function useDayAheadForecast(deliveryDate: string, cutoffTime: CutoffTimeUtc) {
  const result = useApiCall<DayAheadForecastResponse>(
    () => apiFetch(`/forecast/day-ahead?delivery_date=${deliveryDate}&cutoff_time=${cutoffTime}`),
    [deliveryDate, cutoffTime],
  );

  const syncLive = useCallback(async (force = false) => {
    const { data } = await apiFetch<IngestionSyncResponse>('/ingestion/sync-date', {
      method: 'POST',
      body: JSON.stringify({ delivery_date: deliveryDate, force_live_fetch: force }),
    });
    result.refetch();
    return data;
  }, [deliveryDate, result.refetch]);

  return { ...result, syncLive };
}

export function useCutoffPolicy() {
  return useApiCall<CutoffPolicyResponse>(
    () => apiFetch('/cutoffs/policy'),
    [],
  );
}

export function useBenchmark(cutoffTime: CutoffTimeUtc) {
  return useApiCall<JanuaryBenchmarkResponse>(
    () => apiFetch(`/benchmark/january-overview?cutoff_time=${cutoffTime}`),
    [cutoffTime],
  );
}

export function useHealth() {
  return useApiCall<SystemHealthResponse>(
    () => apiFetch('/system/health'),
    [],
  );
}

export function useIngestionCoverage() {
  return useApiCall<IngestionCoverageResponse>(
    () => apiFetch('/ingestion/coverage'),
    [],
  );
}
