export type RankingMetric = 'quality' | 'cost' | 'latency';

export interface RankingBenchmark {
  id: string;
  name: string;
  version: string;
  domain: string;
  collection_id: string;
  collection_hash: string;
  contract_hash: string;
  repetitions: number;
  scenario_count: number;
  experiment_count: number;
  latest_created_at: string;
  benchmark_release_id: string | null;
  benchmark_partition: string | null;
}

export interface RankingSystem {
  id: string;
  system_ids: string[];
  name: string;
  model: string;
  kind: string;
  harness: string;
  fixture_control: boolean;
  submission_replay: boolean;
  external_adapter: boolean;
}

export interface RankingCell {
  benchmark_id: string;
  system_id: string;
  source_system_id: string;
  experiment_id: string;
  experiment_name: string;
  created_at: string;
  status: string;
  pass_rate: number | null;
  passed: number;
  scheduled: number;
  completed: number;
  completion_rate: number | null;
  ci_low: number | null;
  ci_high: number | null;
  interval_method: string;
  known_cost_usd: number;
  cost_usd: number | null;
  cost_complete: boolean;
  cost_per_success_usd: number | null;
  mean_duration_ms: number | null;
  median_duration_ms: number | null;
  p95_duration_ms: number | null;
  duration_complete: boolean;
  duration_recorded: number;
  ranks: Record<RankingMetric, number | null>;
  eligibility: Record<RankingMetric, boolean>;
  issues: string[];
}

export interface RankingsReport {
  schema_version: number;
  benchmarks: RankingBenchmark[];
  systems: RankingSystem[];
  cells: RankingCell[];
  window: {
    limit: number;
    listed_runs: number;
    scanned_runs: number;
    truncated: boolean;
    store_list_limit: number;
  };
  counts: {
    benchmarks: number;
    systems: number;
    cells: number;
    ranked_cells: number;
    experiments_scanned: number;
    experiments_skipped: number;
  };
  warnings: string[];
}
