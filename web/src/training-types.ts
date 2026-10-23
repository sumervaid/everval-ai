/** Read-only projection of the post-training service. No grader outputs or secrets. */
export interface TrainingRunSummary {
  id: string;
  name: string;
  training_run_id: string;
  provenance_kind: string;
  created_at: string;
  checkpoint_count: number;
  status: string;
}
export interface TrainingDomain {
  name: string;
  collection_id: string;
  task_families: Record<string, string>;
  practical_threshold: number;
}
export interface TrainingMetric {
  domain: string;
  experiment_id: string | null;
  status: string;
  value: number | null;
  scheduled_pass_rate?: number | null;
  lower: number | null;
  upper: number | null;
  interval_method?: string;
  n: number;
  expected_n: number;
  known_cost_usd: number;
  cost_complete: boolean;
  unknown_cost_count: number;
  errors: number;
  missing: number;
  lag_seconds: number | null;
}
export interface TrainingCheckpoint {
  id: string;
  step: number;
  weight_sha256: string;
  system_id: string;
  system_hash: string;
  created_at: string;
  status: string;
  metrics: TrainingMetric[];
  known_cost_usd: number;
  unknown_cost_count: number;
  lag_seconds: number | null;
}
export interface TrainingDashboard {
  schema_version: number;
  generated_at?: string;
  run: TrainingRunSummary & {
    protocol: {
      domains: TrainingDomain[];
      inference_scope?: string;
      independence_statement?: string;
      baseline_step: number;
      comparison_steps: number[];
      max_looks: number;
      alpha: number;
      min_clusters: number;
      repetitions: number;
      budget_per_domain_usd: number;
      lifetime_budget_usd: number;
    };
  };
  checkpoints: TrainingCheckpoint[];
  comparisons: TrainingComparison[];
  alerts: Record<string, unknown>[];
  investigations: Record<string, unknown>[];
  qualifications: Record<string, unknown>[];
  mix_studies: Record<string, unknown>[];
  mix_validations?: Record<string, unknown>[];
  budget: {
    allocated_usd: number;
    admission_blocked?: boolean;
    blocking_reasons?: string[];
    conservative_commitment_usd?: number;
    lifetime_budget_usd: number;
    known_cost_usd: number;
    unknown_cost_count: number;
  };
}

export interface TrainingComparison {
  id: string;
  baseline_step: number;
  candidate_step: number;
  look_index: number;
  max_looks: number;
  allocated_alpha: number;
  stale: boolean;
  synthetic_control: boolean;
  inference_scope?: string;
  domains: {
    domain: string;
    decision: string;
    valid: boolean;
    reasons: string[];
    expected_pairs: number;
    matched_pairs: number;
    clusters: number;
    interval: [number, number] | null;
    delta: number | null;
    comparison_alpha: number;
    method: string;
    execution_contract_issues: string[];
    qualification?: { status: string; automatic_trust: boolean };
  }[];
}
