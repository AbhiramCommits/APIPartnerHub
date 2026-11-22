export type CampaignStatus = "active" | "paused" | "ended";
export type CampaignObjective = "awareness" | "consideration" | "conversions" | "retention";
export type AuthMode = "api_key" | "oauth";

export interface PartnerProfile {
  id: number;
  name: string;
  tier: "free" | "standard" | "enterprise";
  rate_limit_rpm: number;
  rate_limit_burst: number;
  scopes: string[];
  auth_mode: AuthMode;
  created_at: string;
}

export interface Campaign {
  id: number;
  name: string;
  status: CampaignStatus;
  daily_budget_cents: number;
  objective: CampaignObjective;
  created_at: string;
  updated_at: string;
}

export interface CampaignListResponse {
  data: Campaign[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface CreateCampaignInput {
  name: string;
  status?: CampaignStatus;
  daily_budget_cents?: number;
  objective?: CampaignObjective;
}

export type UpdateCampaignInput = Partial<CreateCampaignInput>;

export interface MetricRow {
  date?: string;
  campaign_id?: number;
  campaign_name?: string;
  impressions?: number;
  clicks?: number;
  spend_cents?: number;
  conversions?: number;
  ctr_pct: number;
  cpc_cents: number;
  cpm_cents: number;
  cvr_pct: number;
}

export interface MetricsResponse {
  start_date: string;
  end_date: string;
  group_by: "day" | "campaign";
  metrics: string[];
  row_count: number;
  data: MetricRow[];
}

export interface MetricsQuery {
  startDate: string;
  endDate: string;
  groupBy?: "day" | "campaign";
  metrics?: string[];
  campaignIds?: number[];
  asCsv?: boolean;
}

export interface BulkRowError {
  row: number;
  field: string;
  message: string;
}

export interface BulkResult {
  accepted: number;
  rejected: number;
  errors: BulkRowError[];
  created_ids: number[];
}

export interface ErrorDetail {
  field: string;
  message: string;
  value?: unknown;
}

export interface ErrorEnvelope {
  error: {
    type: string;
    message: string;
    code: string;
    doc_url: string;
    request_id: string;
    details: ErrorDetail[];
  };
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  scope: string;
}

export interface ListCampaignsOptions {
  limit?: number;
  status?: CampaignStatus;
  objective?: CampaignObjective;
  nameContains?: string;
}
