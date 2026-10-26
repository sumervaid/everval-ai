export interface Session {
  id: string;
  name: string;
  role: 'admin' | 'operator' | 'reviewer' | 'viewer';
  mode: 'local' | 'legacy' | 'team';
  team_enabled: boolean;
  capabilities: string[];
}
export interface TeamUser {
  id: string;
  name: string;
  role: string;
  active: boolean;
}
export interface RubricDimension {
  id: string;
  name: string;
  guidance: string;
}
export interface Rubric {
  id: string;
  name: string;
  version: string;
  hash: string;
  instructions: string;
  dimensions: RubricDimension[];
}
export interface ReviewAssignment {
  id: string;
  queue_id: string;
  queue_name: string;
  name: string;
  domain: string;
  blind: boolean;
  reviewed: boolean;
}
export interface ReviewJudgment {
  id: string;
  scores: Record<string, Record<string, number>>;
  preference: string | null;
  disposition: string;
  rationale: string;
  reviewer_name: string;
  created_at: string;
}
export interface ReviewItem {
  study_id?: string;
  study_status?: string;
  locked?: boolean;
  id: string;
  queue_name: string;
  blind: boolean;
  rubric: Rubric;
  task: { name: string; domain: string; instruction: string; inputs: Record<string, unknown> };
  responses: Record<string, { output: Record<string, unknown>; name?: string }>;
  previous: ReviewJudgment | null;
  blinding_note: string;
}
export interface QueueSummary {
  id: string;
  name: string;
  blind: boolean;
  created_at: string;
  experiment_id: string;
}
export interface ReviewBrief {
  study_id?: string;
  study_status?: string;
  sealed?: boolean;
  queue_id: string;
  name: string;
  evidence_hash: string;
  rubric: Rubric;
  fixture_controls_present?: boolean;
  submission_replays_present?: boolean;
  assigned_reviews: number;
  completed_reviews: number;
  preferences: Record<string, number>;
  dispositions: Record<string, number>;
  interpretation: string;
  decisions: {
    id: string;
    outcome: string;
    rationale: string;
    created_at: string;
    actor: string;
  }[];
}
export interface ProductionCase {
  id: string;
  name: string;
  domain: string;
  instruction: string;
  inputs: Record<string, unknown>;
  observed_output: Record<string, unknown> | null;
  status: string;
  collection_id?: string;
  approval: { expected_behavior: string; rationale: string } | null;
}
export interface CaseBatch {
  id: string;
  filename: string;
  count: number;
  created_at: string;
  source_hash: string;
  privacy_note: string;
  cases: ProductionCase[];
}
