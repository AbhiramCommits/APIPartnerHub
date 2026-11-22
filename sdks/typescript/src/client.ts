import { APIPartnerHubError, errorFromResponse } from "./errors.js";
import { fetchWithRetry, type RetryOptions } from "./retry.js";
import type {
  BulkResult,
  Campaign,
  CampaignListResponse,
  CreateCampaignInput,
  ListCampaignsOptions,
  MetricsQuery,
  MetricsResponse,
  PartnerProfile,
  TokenResponse,
  UpdateCampaignInput,
} from "./types.js";

export interface APIPartnerHubClientOptions {
  baseUrl?: string;
  apiKey?: string;
  clientId?: string;
  clientSecret?: string;
  fetchImpl?: typeof fetch;
  retry?: RetryOptions;
  now?: () => number;
}

interface RequestOptions {
  query?: Record<string, unknown>;
  json?: unknown;
  body?: BodyInit;
  headers?: HeadersInit;
}

interface RawResult<T> {
  data: T;
  attempts: number;
  status: number;
  headers: Headers;
}

const TOKEN_SAFETY_WINDOW_MS = 60_000;

export class APIPartnerHubClient {
  readonly baseUrl: string;
  readonly usesOauth: boolean;
  lastAttempts = 0;
  lastRequestId: string | null = null;

  private readonly apiKey?: string;
  private readonly clientId?: string;
  private readonly clientSecret?: string;
  private readonly fetchImpl: typeof fetch;
  private readonly retryOptions: RetryOptions;
  private readonly now: () => number;
  private token: string | null = null;
  private tokenExpiresAt = 0;

  constructor(options: APIPartnerHubClientOptions = {}) {
    const hasApiKey = Boolean(options.apiKey);
    const hasOauth = Boolean(options.clientId && options.clientSecret);
    if (!hasApiKey && !hasOauth) {
      throw new Error("Provide apiKey or both clientId and clientSecret.");
    }
    if (hasApiKey && (options.clientId || options.clientSecret)) {
      throw new Error("Use either apiKey or OAuth client credentials, not both.");
    }
    this.baseUrl = (options.baseUrl ?? "http://localhost:8000").replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.clientId = options.clientId;
    this.clientSecret = options.clientSecret;
    this.usesOauth = !hasApiKey;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
    this.retryOptions = options.retry ?? {};
    this.now = options.now ?? Date.now;
  }

  private async ensureToken(force = false): Promise<string> {
    if (!this.usesOauth) {
      throw new APIPartnerHubError("This client uses an API key, not OAuth.");
    }
    if (
      !force &&
      this.token !== null &&
      this.now() < this.tokenExpiresAt - TOKEN_SAFETY_WINDOW_MS
    ) {
      return this.token;
    }
    await this.requestToken(force);
    if (this.token === null) {
      throw new APIPartnerHubError("OAuth response did not include access_token.");
    }
    return this.token;
  }

  private async requestToken(force = false): Promise<void> {
    if (
      !force &&
      this.token !== null &&
      this.now() < this.tokenExpiresAt - TOKEN_SAFETY_WINDOW_MS
    ) {
      return;
    }
    const body = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: this.clientId ?? "",
      client_secret: this.clientSecret ?? "",
    });
    let result;
    try {
      result = await fetchWithRetry(
        () =>
          this.fetchImpl(`${this.baseUrl}/oauth/token`, {
            method: "POST",
            headers: { "content-type": "application/x-www-form-urlencoded" },
            body,
          }),
        this.retryOptions,
      );
    } catch (error) {
      if (error instanceof APIPartnerHubError) throw error;
      throw new APIPartnerHubError(
        `Could not reach the OAuth token endpoint: ${String(error)}`,
        { type: "connection_error" },
      );
    }
    const payload = await this.parseBody(result.response);
    if (!result.response.ok) {
      throw errorFromResponse(result.response.status, payload, result.response.headers);
    }
    const tokenPayload = payload as TokenResponse;
    this.token = tokenPayload.access_token;
    this.tokenExpiresAt = this.now() + (tokenPayload.expires_in ?? 3600) * 1000;
  }

  private async authHeaders(force = false): Promise<Record<string, string>> {
    if (this.apiKey) return { "X-API-Key": this.apiKey };
    return { Authorization: `Bearer ${await this.ensureToken(force)}` };
  }

  private buildUrl(path: string, query?: Record<string, unknown>): string {
    const url = new URL(`${this.baseUrl}${path}`);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value === undefined || value === null) continue;
        if (Array.isArray(value)) {
          for (const item of value) url.searchParams.append(key, String(item));
        } else {
          url.searchParams.set(key, String(value));
        }
      }
    }
    return url.toString();
  }

  private async parseBody(response: Response): Promise<unknown> {
    const text = await response.text();
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }

  async request<T>(
    method: string,
    path: string,
    options: RequestOptions = {},
  ): Promise<RawResult<T>> {
    let attempts = 0;
    let forceRefresh = false;
    let response: Response | null = null;

    for (let authAttempt = 0; authAttempt < 2; authAttempt += 1) {
      const headers = new Headers(options.headers);
      for (const [key, value] of Object.entries(await this.authHeaders(forceRefresh))) {
        headers.set(key, value);
      }
      let body = options.body;
      if (options.json !== undefined) {
        headers.set("content-type", "application/json");
        body = JSON.stringify(options.json);
      }
      const url = this.buildUrl(path, options.query);
      const result = await fetchWithRetry(
        () =>
          this.fetchImpl(url, {
            method,
            headers,
            body,
          }),
        this.retryOptions,
      );
      attempts += result.attempts;
      response = result.response;
      if (response.status === 401 && this.usesOauth && authAttempt === 0) {
        forceRefresh = true;
        continue;
      }
      break;
    }

    if (response === null) {
      throw new APIPartnerHubError("Request did not produce a response.");
    }
    this.lastAttempts = attempts;
    this.lastRequestId = response.headers.get("x-request-id");
    const payload = await this.parseBody(response);
    if (!response.ok) {
      throw errorFromResponse(response.status, payload, response.headers);
    }
    return {
      data: payload as T,
      attempts,
      status: response.status,
      headers: response.headers,
    };
  }

  async getMe(): Promise<PartnerProfile> {
    return (await this.request<PartnerProfile>("GET", "/v1/partners/me")).data;
  }

  async *listCampaigns(options: ListCampaignsOptions = {}): AsyncGenerator<Campaign> {
    let cursor: string | null = null;
    do {
      const query: Record<string, unknown> = { limit: options.limit ?? 100 };
      if (options.status) query.status = options.status;
      if (options.objective) query.objective = options.objective;
      if (options.nameContains) query.name_contains = options.nameContains;
      if (cursor) query.cursor = cursor;
      const page = await this.request<CampaignListResponse>("GET", "/v1/campaigns", {
        query,
      });
      for (const campaign of page.data.data) yield campaign;
      cursor = page.data.has_more ? page.data.next_cursor : null;
    } while (cursor);
  }

  async createCampaign(
    input: CreateCampaignInput,
    idempotencyKey?: string,
  ): Promise<Campaign> {
    const key = idempotencyKey ?? globalThis.crypto.randomUUID();
    return (
      await this.request<Campaign>("POST", "/v1/campaigns", {
        json: input,
        headers: { "Idempotency-Key": key },
      })
    ).data;
  }

  async getCampaign(campaignId: number): Promise<Campaign> {
    return (await this.request<Campaign>("GET", `/v1/campaigns/${campaignId}`)).data;
  }

  async updateCampaign(
    campaignId: number,
    fields: UpdateCampaignInput,
    ifMatch?: string,
  ): Promise<Campaign> {
    if (Object.keys(fields).length === 0) {
      throw new Error("updateCampaign requires at least one field.");
    }
    const headers: Record<string, string> = {};
    if (ifMatch) headers["If-Match"] = ifMatch;
    return (
      await this.request<Campaign>("PATCH", `/v1/campaigns/${campaignId}`, {
        json: fields,
        headers,
      })
    ).data;
  }

  async getMetrics(query: MetricsQuery): Promise<MetricsResponse | string> {
    const params: Record<string, unknown> = {
      start_date: query.startDate,
      end_date: query.endDate,
      group_by: query.groupBy ?? "day",
    };
    if (query.metrics?.length) params.metrics = query.metrics.join(",");
    if (query.campaignIds?.length) params.campaign_ids = query.campaignIds;
    if (query.asCsv) {
      params.format = "csv";
      return (
        await this.request<string>("GET", "/v1/reports/metrics", {
          query: params,
        })
      ).data;
    }
    return (await this.request<MetricsResponse>("GET", "/v1/reports/metrics", { query: params }))
      .data;
  }

  async bulkUploadCampaigns(file: Blob, filename = "campaigns.csv"): Promise<BulkResult> {
    const form = new FormData();
    form.append("file", file, filename);
    return (
      await this.request<BulkResult>("POST", "/v1/bulk/campaigns", { body: form })
    ).data;
  }
}
