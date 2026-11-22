import type { ErrorDetail, ErrorEnvelope } from "./types.js";

export class APIPartnerHubError extends Error {
  readonly statusCode: number | null;
  readonly type: string | null;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly docUrl: string | null;
  readonly details: ErrorDetail[];
  readonly retryAfter: number | null;

  constructor(
    message: string,
    options: {
      statusCode?: number | null;
      type?: string | null;
      code?: string | null;
      requestId?: string | null;
      docUrl?: string | null;
      details?: ErrorDetail[];
      retryAfter?: number | null;
    } = {},
  ) {
    super(message);
    this.name = new.target.name;
    this.statusCode = options.statusCode ?? null;
    this.type = options.type ?? null;
    this.code = options.code ?? null;
    this.requestId = options.requestId ?? null;
    this.docUrl = options.docUrl ?? null;
    this.details = options.details ?? [];
    this.retryAfter = options.retryAfter ?? null;
  }
}

export class AuthenticationError extends APIPartnerHubError {}
export class ForbiddenError extends APIPartnerHubError {}
export class NotFoundError extends APIPartnerHubError {}
export class ConflictError extends APIPartnerHubError {}
export class ValidationError extends APIPartnerHubError {}
export class RateLimitedError extends APIPartnerHubError {}
export class ServerError extends APIPartnerHubError {}
export class APIConnectionError extends APIPartnerHubError {
  readonly attempts: number;

  constructor(message: string, attempts = 0) {
    super(message, { type: "connection_error" });
    this.attempts = attempts;
  }
}

const CLASS_BY_STATUS: Record<number, typeof APIPartnerHubError> = {
  401: AuthenticationError,
  403: ForbiddenError,
  404: NotFoundError,
  409: ConflictError,
  422: ValidationError,
  429: RateLimitedError,
};

export function errorFromResponse(
  status: number,
  body: unknown,
  headers: Headers,
): APIPartnerHubError {
  const requestId = headers.get("x-request-id");
  const retryAfterRaw = headers.get("retry-after");
  const retryAfter = retryAfterRaw === null ? null : Number(retryAfterRaw);
  const ErrorClass = CLASS_BY_STATUS[status] ?? (status >= 500 ? ServerError : APIPartnerHubError);

  const envelope = extractEnvelope(body);
  if (envelope) {
    return new ErrorClass(envelope.error.message, {
      statusCode: status,
      type: envelope.error.type,
      code: envelope.error.code,
      requestId: envelope.error.request_id ?? requestId,
      docUrl: envelope.error.doc_url,
      details: envelope.error.details ?? [],
      retryAfter,
    });
  }

  if (typeof body === "object" && body !== null && "error" in body) {
    const rfc = body as { error: unknown; error_description?: unknown };
    if (typeof rfc.error === "string") {
      return new APIPartnerHubError(
        `${rfc.error}: ${String(rfc.error_description ?? rfc.error)}`,
        { statusCode: status, type: rfc.error, requestId, retryAfter },
      );
    }
  }

  return new ErrorClass(`HTTP ${status} response did not contain the API error envelope.`, {
    statusCode: status,
    type: "http_error",
    code: `HTTP-${status}`,
    requestId,
    retryAfter,
  });
}

function extractEnvelope(body: unknown): ErrorEnvelope | null {
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return null;
  }
  const candidate = (body as { error: unknown }).error;
  if (typeof candidate !== "object" || candidate === null) {
    return null;
  }
  const envelope = candidate as Partial<ErrorEnvelope["error"]>;
  if (typeof envelope.message !== "string" || typeof envelope.type !== "string") {
    return null;
  }
  return {
    error: {
      type: envelope.type,
      message: envelope.message,
      code: envelope.code ?? "",
      doc_url: envelope.doc_url ?? "",
      request_id: envelope.request_id ?? "",
      details: envelope.details ?? [],
    },
  };
}
