import { APIConnectionError } from "./errors.js";

export interface RetryOptions {
  maxRetries?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  retryStatuses?: Set<number>;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
  respectRetryAfter?: boolean;
}

export interface ResolvedRetryOptions {
  maxRetries: number;
  baseDelayMs: number;
  maxDelayMs: number;
  retryStatuses: Set<number>;
  sleep: (ms: number) => Promise<void>;
  random: () => number;
  respectRetryAfter: boolean;
}

export interface FetchWithRetryResult {
  response: Response;
  attempts: number;
  totalDelayMs: number;
}

export const DEFAULT_RETRY_STATUSES = new Set([429, 502, 503, 504]);

export function resolveRetryOptions(options: RetryOptions = {}): ResolvedRetryOptions {
  return {
    maxRetries: options.maxRetries ?? 3,
    baseDelayMs: options.baseDelayMs ?? 500,
    maxDelayMs: options.maxDelayMs ?? 8000,
    retryStatuses: options.retryStatuses ?? DEFAULT_RETRY_STATUSES,
    sleep:
      options.sleep ??
      ((ms: number) => new Promise((resolve) => setTimeout(resolve, ms))),
    random: options.random ?? Math.random,
    respectRetryAfter: options.respectRetryAfter ?? true,
  };
}

export function retryAfterMs(response: Response): number | null {
  const raw = response.headers.get("retry-after");
  if (!raw) return null;
  const seconds = Number(raw);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const when = Date.parse(raw);
  if (Number.isNaN(when)) return null;
  return Math.max(0, when - Date.now());
}

export function delayForAttempt(
  attemptIndex: number,
  options: ResolvedRetryOptions,
): number {
  const ceiling = Math.min(
    options.maxDelayMs,
    options.baseDelayMs * 2 ** attemptIndex,
  );
  return options.random() * ceiling;
}

export function delayForResponse(
  response: Response,
  attemptIndex: number,
  options: ResolvedRetryOptions,
): number {
  if (options.respectRetryAfter) {
    const specified = retryAfterMs(response);
    if (specified !== null) return specified;
  }
  return delayForAttempt(attemptIndex, options);
}

export async function fetchWithRetry(
  fetchFn: () => Promise<Response>,
  options: RetryOptions = {},
): Promise<FetchWithRetryResult> {
  const resolved = resolveRetryOptions(options);
  let totalDelayMs = 0;

  for (let attempt = 0; attempt <= resolved.maxRetries; attempt += 1) {
    const attempts = attempt + 1;
    let response: Response;
    try {
      response = await fetchFn();
    } catch (error) {
      if (attempt >= resolved.maxRetries) {
        throw new APIConnectionError(
          `Request failed after ${attempts} attempt(s): ${String(error)}`,
          attempts,
        );
      }
      const delay = delayForAttempt(attempt, resolved);
      await resolved.sleep(delay);
      totalDelayMs += delay;
      continue;
    }

    if (resolved.retryStatuses.has(response.status) && attempt < resolved.maxRetries) {
      const delay = delayForResponse(response, attempt, resolved);
      await resolved.sleep(delay);
      totalDelayMs += delay;
      continue;
    }
    return { response, attempts, totalDelayMs: Math.round(totalDelayMs) };
  }

  throw new APIConnectionError("Retry loop exhausted without a response.");
}
