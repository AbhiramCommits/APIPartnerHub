import { describe, expect, it } from "vitest";

import {
  RateLimitedError,
  ValidationError,
  errorFromResponse,
} from "../src/errors.js";
import {
  delayForAttempt,
  fetchWithRetry,
  resolveRetryOptions,
} from "../src/retry.js";

function response(status: number, headers: Record<string, string> = {}): Response {
  return new Response("{}", { status, headers });
}

describe("fetchWithRetry", () => {
  it("retries a 429 and honours Retry-After exactly", async () => {
    const sleeps: number[] = [];
    let calls = 0;
    const result = await fetchWithRetry(
      async () => {
        calls += 1;
        return calls === 1
          ? response(429, { "Retry-After": "2" })
          : response(200);
      },
      {
        maxRetries: 3,
        sleep: async (ms) => {
          sleeps.push(ms);
        },
      },
    );

    expect(result.response.status).toBe(200);
    expect(result.attempts).toBe(2);
    expect(sleeps).toEqual([2000]);
    expect(result.totalDelayMs).toBe(2000);
  });

  it("retries 503 and 504 but stops at maxRetries", async () => {
    const sleeps: number[] = [];
    let calls = 0;
    const result = await fetchWithRetry(
      async () => {
        calls += 1;
        return response(calls === 1 ? 503 : calls === 2 ? 504 : 200);
      },
      {
        maxRetries: 3,
        random: () => 0.5,
        baseDelayMs: 100,
        sleep: async (ms) => {
          sleeps.push(ms);
        },
      },
    );

    expect(result.attempts).toBe(3);
    expect(sleeps).toEqual([50, 100]);
  });

  it("returns the final retryable response when attempts run out", async () => {
    const result = await fetchWithRetry(async () => response(503), {
      maxRetries: 2,
      random: () => 0,
      sleep: async () => undefined,
    });
    expect(result.attempts).toBe(3);
    expect(result.response.status).toBe(503);
  });

  it("retries transport errors with full jitter", async () => {
    let calls = 0;
    const sleeps: number[] = [];
    const result = await fetchWithRetry(
      async () => {
        calls += 1;
        if (calls === 1) throw new Error("ECONNRESET");
        return response(200);
      },
      {
        maxRetries: 2,
        baseDelayMs: 100,
        random: () => 0.5,
        sleep: async (ms) => {
          sleeps.push(ms);
        },
      },
    );
    expect(result.attempts).toBe(2);
    expect(sleeps).toEqual([50]);
  });

  it("computes exponential full-jitter ceilings", () => {
    const options = resolveRetryOptions({
      baseDelayMs: 100,
      maxDelayMs: 300,
      random: () => 0.5,
      sleep: async () => undefined,
    });
    expect(delayForAttempt(0, options)).toBe(50);
    expect(delayForAttempt(1, options)).toBe(100);
    expect(delayForAttempt(2, options)).toBe(150);
    expect(delayForAttempt(5, options)).toBe(150);
  });
});

describe("error mapping", () => {
  it("maps a 429 envelope to RateLimitedError with retryAfter and requestId", () => {
    const error = errorFromResponse(
      429,
      {
        error: {
          type: "rate_limited",
          message: "Rate limit exceeded.",
          code: "RL-001",
          doc_url: "https://docs.apipartnerhub.dev/errors#rate_limited",
          request_id: "req-123",
          details: [{ field: "rate_limit", message: "retry later" }],
        },
      },
      new Headers({ "retry-after": "3", "x-request-id": "req-123" }),
    );

    expect(error).toBeInstanceOf(RateLimitedError);
    expect(error.statusCode).toBe(429);
    expect(error.code).toBe("RL-001");
    expect(error.retryAfter).toBe(3);
    expect(error.requestId).toBe("req-123");
    expect(error.details).toHaveLength(1);
  });

  it("maps 422 row-level details to ValidationError", () => {
    const error = errorFromResponse(
      422,
      {
        error: {
          type: "validation_failed",
          message: "CSV rows failed validation.",
          code: "VAL-001",
          request_id: "req-422",
          details: [{ row: 4, field: "status", message: "bad status" }],
        },
      },
      new Headers(),
    );

    expect(error).toBeInstanceOf(ValidationError);
    expect(error.details[0]).toMatchObject({ row: 4, field: "status" });
  });

  it("maps RFC OAuth errors", () => {
    const error = errorFromResponse(
      401,
      { error: "invalid_client", error_description: "Client authentication failed." },
      new Headers(),
    );
    expect(error.statusCode).toBe(401);
    expect(error.type).toBe("invalid_client");
    expect(error.message).toContain("Client authentication failed");
  });

  it("maps unknown failures to a generic error with the status", () => {
    const error = errorFromResponse(502, "upstream down", new Headers());
    expect(error.statusCode).toBe(502);
    expect(error.type).toBe("http_error");
    expect(error.code).toBe("HTTP-502");
  });
});
