import { useState } from 'react';
import { request } from '../api';
import { Button, Field, Notice, Panel } from '../components';
import { errorMessage, money } from '../format';
import { useRemote } from './useRemote';
interface Limit {
  scope: string;
  daily_usd: number | null;
  monthly_usd: number | null;
  lifetime_usd: number | null;
  usage: Record<string, number>;
}
export function SpendingLimits() {
  const limits = useRemote<Limit[]>('/spending-limits');
  const projects = useRemote<{ id: string; name: string }[]>('/projects');
  const [scope, setScope] = useState('workspace');
  const [daily, setDaily] = useState('');
  const [monthly, setMonthly] = useState('');
  const [lifetime, setLifetime] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const select = (value: string) => {
    const previous = limits.data?.find((limit) => limit.scope === value);
    setScope(value);
    setDaily(previous?.daily_usd?.toString() ?? '');
    setMonthly(previous?.monthly_usd?.toString() ?? '');
    setLifetime(previous?.lifetime_usd?.toString() ?? '');
    setSaved(false);
  };
  return (
    <Panel
      title="Cumulative spending limits"
      detail="Workspace and repository limits govern new paid admissions across experiments."
    >
      <div className="team-panel-body team-stack">
        {limits.data?.map((limit) => (
          <div key={limit.scope} className="spend-limit-row">
            <strong>
              {limit.scope === 'workspace'
                ? 'Whole workspace'
                : (projects.data?.find((project) => 'project:' + project.id === limit.scope)
                    ?.name ?? limit.scope)}
            </strong>
            <span>
              Day: {money(limit.usage.daily_usd)} /{' '}
              {limit.daily_usd === null ? 'uncapped' : money(limit.daily_usd)}
            </span>
            <span>
              Month: {money(limit.usage.monthly_usd)} /{' '}
              {limit.monthly_usd === null ? 'uncapped' : money(limit.monthly_usd)}
            </span>
            <span>
              Lifetime: {money(limit.usage.lifetime_usd)} /{' '}
              {limit.lifetime_usd === null ? 'uncapped' : money(limit.lifetime_usd)}
            </span>
            <Button variant="ghost" onClick={() => select(limit.scope)}>
              Edit this limit
            </Button>
          </div>
        ))}
        {!limits.data?.length && <p className="muted">No cumulative limits are configured.</p>}
        <form
          className="team-stack"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            setSaved(false);
            try {
              await request('/spending-limits', {
                method: 'PUT',
                body: JSON.stringify({
                  scope,
                  daily_usd: daily === '' ? null : Number(daily),
                  monthly_usd: monthly === '' ? null : Number(monthly),
                  lifetime_usd: lifetime === '' ? null : Number(lifetime),
                }),
              });
              limits.reload();
              setSaved(true);
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Spending scope">
            <select value={scope} onChange={(event) => select(event.target.value)}>
              <option value="workspace">Whole workspace</option>
              {projects.data?.map((project) => (
                <option key={project.id} value={'project:' + project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </Field>
          <div className="team-form-grid">
            <Field label="Daily limit · USD">
              <input
                type="number"
                min={0}
                step="any"
                value={daily}
                onChange={(event) => setDaily(event.target.value)}
              />
            </Field>
            <Field label="Monthly limit · USD">
              <input
                type="number"
                min={0}
                step="any"
                value={monthly}
                onChange={(event) => setMonthly(event.target.value)}
              />
            </Field>
            <Field label="Lifetime limit · USD">
              <input
                type="number"
                min={0}
                step="any"
                value={lifetime}
                onChange={(event) => setLifetime(event.target.value)}
              />
            </Field>
          </div>
          <p className="muted">
            Blank removes that limit; zero blocks paid admissions. Daily and monthly periods use
            UTC. Committed spending includes unsettled reservations.
          </p>
          {(error || limits.error || projects.error) && (
            <Notice tone="error">{error || limits.error || projects.error}</Notice>
          )}
          {saved && <Notice tone="success">Spending limits saved.</Notice>}
          <Button type="submit" busy={busy}>
            Save cumulative limits
          </Button>
        </form>
      </div>
    </Panel>
  );
}
