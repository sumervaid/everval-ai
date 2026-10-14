export const percent = (value: number | null | undefined) =>
  value == null ? '—' : `${(value * 100).toFixed(1)}%`;
export const money = (value: number | null | undefined) =>
  value == null
    ? '—'
    : new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: value > 0 && value < 0.01 ? 6 : 2,
      }).format(value);
export const duration = (ms: number | null | undefined) =>
  ms == null
    ? '—'
    : ms < 1000
      ? `${Math.round(ms)} ms`
      : ms < 60000
        ? `${(ms / 1000).toFixed(1)} s`
        : `${(ms / 60000).toFixed(1)} min`;
export const date = (value: string | null | undefined) => {
  if (!value) return '—';
  const d = new Date(value.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(value) ? value : value + 'Z');
  return Number.isNaN(d.getTime())
    ? value
    : new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      }).format(d);
};
export const bytes = (value: number) =>
  value > 1024 ** 4
    ? `${(value / 1024 ** 4).toFixed(1)} TB`
    : `${(value / 1024 ** 3).toFixed(1)} GB`;
export const json = (value: unknown) => JSON.stringify(value, null, 2) ?? 'null';
export const domainLabel: Record<string, string> = {
  coding: 'Code',
  documents: 'Documents',
  data: 'Structured data',
  workflow: 'Workflows',
  mixed: 'Mixed domains',
};
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}
export function parseObject(value: string, field: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed))
    throw new Error(`${field} must be a JSON object.`);
  return parsed as Record<string, unknown>;
}
export function isActive(status: string): boolean {
  return status === 'queued' || status === 'running';
}
