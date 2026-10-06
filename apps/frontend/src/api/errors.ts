import type { ApiError } from '@studgroup/shared-types';

export type ApiFailureKind = 'network' | 'http';

/** Normalised request failure. `error` is the contract's ApiError when the backend sent one. */
export class ApiRequestError extends Error {
  readonly kind: ApiFailureKind;
  readonly status: number | null;
  readonly error: ApiError | null;

  constructor(kind: ApiFailureKind, status: number | null, error: ApiError | null) {
    super(error?.code ?? (kind === 'network' ? 'network_error' : `http_${status}`));
    this.name = 'ApiRequestError';
    this.kind = kind;
    this.status = status;
    this.error = error;
  }

  get code(): ApiError['code'] | null {
    return this.error?.code ?? null;
  }
  get correlationId(): string | null {
    return this.error?.correlation_id ?? null;
  }
  get retryable(): boolean {
    if (this.kind === 'network') return true;
    if (this.error) return this.error.retryable;
    return this.status !== null && this.status >= 500;
  }
  get retryAfterSeconds(): number | null {
    return this.error?.retry_after_seconds ?? null;
  }
}

export function isApiRequestError(value: unknown): value is ApiRequestError {
  return value instanceof ApiRequestError;
}

function parseApiError(body: unknown): ApiError | null {
  if (typeof body !== 'object' || body === null || !('error' in body)) return null;
  const err = (body as { error: unknown }).error;
  if (typeof err !== 'object' || err === null) return null;
  const e = err as Record<string, unknown>;
  // Tolerant: unknown codes still pass; screens fall back to a generic state.
  if (typeof e.code !== 'string' || typeof e.message !== 'string') return null;
  return {
    code: e.code as ApiError['code'],
    message: e.message,
    correlation_id: typeof e.correlation_id === 'string' ? e.correlation_id : '',
    retryable: e.retryable === true,
    retry_after_seconds: typeof e.retry_after_seconds === 'number' ? e.retry_after_seconds : null,
    details: typeof e.details === 'object' ? (e.details as ApiError['details']) : null,
  };
}

interface FieldsResult<T> {
  data?: T;
  error?: unknown;
  response?: Response;
}

/** Unwraps the generated SDK's `fields` result, turning failures into ApiRequestError. */
export async function unwrap<T>(request: Promise<FieldsResult<T>>): Promise<T> {
  let result: FieldsResult<T>;
  try {
    result = await request;
  } catch {
    throw new ApiRequestError('network', null, null);
  }
  if (result.error !== undefined || !result.response?.ok) {
    throw new ApiRequestError('http', result.response?.status ?? null, parseApiError(result.error));
  }
  if (result.data === undefined) throw new ApiRequestError('http', result.response.status, null);
  return result.data;
}
