export type TranscriptLine = {
  speaker: string;
  role: string;
  text: string;
  timestamp: string;
};

export type Evidence = {
  source: string;
  generated_at: string;
  campaign: string;
  metric: string;
  actual: number;
  target: number;
  direction: "min" | "max";
  unit: string;
};

export type AnalysisResult = {
  should_respond: boolean;
  severity: "silent" | "info" | "warning" | "critical";
  category: string;
  message: string;
  tag: string;
  sentiment: string | null;
  voice_intervention: boolean;
  voice_suppressed_reason?: string | null;
  evidence: Evidence[];
  executive_summary?: string | null;
  recommended_actions?: string[];
  executive_profile?: ExecutiveProfile;
  source_status?: DataSourceStatus;
};

export type VigIAResponse = {
  tag: string;  // "💬 ANÁLISIS", "⚠️ ALERTA", "💡 SUGERENCIA", "[EMOJI] SENTIMIENTO"
  message: string;
  should_respond: boolean;
  severity: "silent" | "info" | "warning" | "critical";
  category: string;
  sentiment: string | null;
  voice_intervention: boolean;
  voice_suppressed_reason?: string | null;
  evidence: Evidence[];
  executive_summary?: string | null;
  recommended_actions?: string[];
  executive_profile?: ExecutiveProfile;
  source_status?: DataSourceStatus;
};

export type Sentiment = {
  emoji: string;
  label: string;
};

export type SessionStatus = "idle" | "active" | "paused" | "closed";
export type ExecutiveProfile = "cfo" | "coo" | "balanced";
export type InterventionLevel = "warning" | "critical" | "manual";

export type SessionState = {
  status: SessionStatus;
  meeting_type: string;
  date: string;
  participants: Participant[];
  voice_mode: boolean;
  executive_profile: ExecutiveProfile;
  intervention_level: InterventionLevel;
  started_at: string | null;
};

export type Participant = {
  name: string;
  role: string;
};

export type PostMeetingDocuments = {
  executive_minutes: string;
  financial_summary: string;
  sentiment_report: string;
  structured_transcript: string;
};

export type CampaignMetric = {
  label: string;
  actual: number;
  target: number;
  direction: "min" | "max";
  unit: string;
};

export type Campaign = {
  id: string;
  name: string;
  owner: string;
  metrics: Record<string, CampaignMetric>;
};

export type BiSnapshot = {
  generated_at: string;
  source: string;
  period?: string;
  campaigns: Campaign[];
  scope_options?: FinancialScopeOptions;
};

export type FinancialScopeCampaign = {
  value: string;
  label: string;
  account: string;
  operation_type: string;
};

export type FinancialScopeOptions = {
  accounts: string[];
  campaigns: FinancialScopeCampaign[];
};

export type FinancialScopeSelection = {
  accounts: string[];
  campaigns: string[];
};

export type DataSourceStatus = {
  provider: "json" | "http" | string;
  available: boolean;
  trusted: boolean;
  stale: boolean;
  source: string | null;
  period?: string | null;
  account_count?: number;
  generated_at: string | null;
  age_seconds: number | null;
  max_age_seconds: number;
  last_refresh_at: string | null;
  last_error: string | null;
};
