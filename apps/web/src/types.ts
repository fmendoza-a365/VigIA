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
  evidence: Evidence[];
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
  campaigns: Campaign[];
};

