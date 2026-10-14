export type Domain = 'coding' | 'documents' | 'data' | 'workflow' | 'mixed';
export interface Check {
  id: string;
  name: string;
  kind:
    | 'equals'
    | 'contains'
    | 'numeric'
    | 'json_schema'
    | 'citation'
    | 'forbidden'
    | 'python_tests';
  path: string;
  expected: unknown;
  weight: number;
  required: boolean;
  tolerance: number;
}
export interface Scenario {
  id: string;
  name: string;
  domain: Domain;
  description: string;
  instruction: string;
  inputs: Record<string, unknown>;
  expected: Record<string, unknown>;
  checks: Check[];
  reference_output: Record<string, unknown> | null;
  tags: string[];
  provenance: Record<string, unknown>;
}
export interface Collection {
  id: string;
  name: string;
  description: string;
  domain: Domain;
  version: string | number;
  created_at: string;
  scenarios: Scenario[];
}
export interface System {
  id: string;
  name: string;
  kind: 'reference' | 'incomplete' | 'http' | 'replay';
  model: string;
  base_url: string;
  api_key_env: string;
  temperature: number | null;
  token_limit_field?: 'max_completion_tokens' | 'max_tokens';
  max_tokens: number;
  input_cost_per_million: number | null;
  output_cost_per_million: number | null;
  configured?: boolean;
  harness?: 'single-turn' | 'tools' | 'external-agent';
  adapter_revision?: string;
}
export interface CheckResult {
  id: string;
  name: string;
  kind: string;
  passed: boolean;
  score: number;
  expected: unknown;
  actual: unknown;
  message: string;
  required: boolean;
}
export interface Trial {
  id: string;
  experiment_id: string;
  scenario_id: string;
  scenario_name: string;
  domain: Domain;
  system_id: string;
  system_name: string;
  repetition: number;
  status: string;
  score: number | null;
  passed: boolean | null;
  duration_ms: number | null;
  cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  output: Record<string, unknown> | null;
  checks: CheckResult[];
  error: string | null;
  started_at: string;
  finished_at: string;
}
export interface SystemReport {
  system_id: string;
  name: string;
  scheduled: number;
  completed: number;
  passed: number;
  failed: number;
  errors: number;
  pass_rate: number | null;
  score: number | null;
  ci_low: number | null;
  ci_high: number | null;
  interval_method?: string;
  cost_usd: number | null;
  cost_complete: boolean;
  cost_per_pass: number | null;
  median_duration_ms: number | null;
  p95_duration_ms: number | null;
}
export interface Report {
  experiment_id: string;
  total_trials: number;
  completed_trials: number;
  incomplete_trials: number;
  systems: SystemReport[];
  comparisons: {
    left: string;
    right: string;
    paired_tasks: number;
    difference: number | null;
    ci_low: number | null;
    ci_high: number | null;
    conclusion: string;
  }[];
  domains: { domain: Domain; system_id: string; trials: number; pass_rate: number | null }[];
  warnings: string[];
}
export interface Experiment {
  id: string;
  name: string;
  collection_id: string;
  collection_version: string | number;
  collection_hash: string;
  system_ids: string[];
  repetitions: number;
  budget_usd: number;
  status: string;
  created_at: string;
  started_at: string;
  finished_at: string;
  total_trials: number;
  completed_trials: number;
  systems: System[];
  trials: Trial[];
  report: Report | null;
}
export interface Overview {
  collections: number;
  scenarios: number;
  experiments: number;
  completed_experiments: number;
  pass_rate: number | null;
  total_cost_usd: number | null;
  recent_experiments: Experiment[];
}
export interface Settings {
  data_dir: string;
  version: string;
  python_version: string;
  storage_free_bytes: number;
  docker_available: boolean;
  remote_providers_enabled: boolean;
  worker_running: boolean;
  auth_enabled: boolean;
}
export interface Validation {
  valid: boolean;
  scenarios: { id: string; valid: boolean; errors: string[]; checks: unknown[] }[];
}
export interface Workspace {
  overview: Overview;
  collections: Collection[];
  systems: System[];
  experiments: Experiment[];
  settings: Settings;
}
