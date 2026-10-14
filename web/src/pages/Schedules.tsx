import { useEffect, useState } from 'react';
import { CalendarClock, Pause, Play, Plus, X } from 'lucide-react';
import { post, request } from '../api';
import type { Experiment } from '../types';
import { date, errorMessage, money } from '../format';
import { Button, EmptyState, Field, Notice, Panel } from '../components';

interface Schedule {
  id: string;
  name: string;
  experiment_id: string;
  interval_minutes: number;
  enabled: boolean;
  next_run_at?: string;
  last_run_at?: string;
  last_error?: string | null;
}
export function ScheduleForm({
  experiment,
  onClose,
}: {
  experiment: Experiment;
  onClose: () => void;
}) {
  const [name, setName] = useState(`${experiment.name} · recurring`);
  const [minutes, setMinutes] = useState('60');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  return (
    <Panel
      title="Repeat this evaluation"
      detail="Create a recurring run from the frozen experiment configuration."
      action={
        <button className="icon-button" aria-label="Close schedule form" onClick={onClose}>
          <X size={17} />
        </button>
      }
    >
      <div className="panel-body">
        {saved ? (
          <Notice tone="success">
            Schedule created. Manage its status in <a href="#/settings">Settings & setup</a>.
          </Notice>
        ) : (
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError('');
              try {
                await post('/schedules', {
                  name: name.trim(),
                  experiment_id: experiment.id,
                  interval_minutes: Number(minutes),
                  enabled: true,
                });
                setSaved(true);
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Notice tone="warning">
              Each scheduled run uses this experiment’s provider budget of{' '}
              <strong>{money(experiment.budget_usd)}</strong>. The local service must remain
              running. Disable the schedule in Settings to stop future runs.
            </Notice>
            {error && <Notice tone="error">{error}</Notice>}
            <div className="form-grid">
              <Field label="Schedule name" required>
                <input
                  required
                  maxLength={160}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Field label="Run every · minutes" hint="Minimum interval: 5 minutes.">
                <input
                  required
                  type="number"
                  min="5"
                  max="525600"
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value)}
                />
              </Field>
            </div>
            <Button type="submit" busy={busy}>
              <Plus size={15} />
              Create recurring schedule
            </Button>
          </form>
        )}
      </div>
    </Panel>
  );
}
export function SchedulesPanel() {
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    request<Schedule[]>('/schedules', { signal: controller.signal })
      .then(setSchedules)
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      });
    return () => controller.abort();
  }, []);
  return (
    <Panel
      title="Recurring evaluations"
      detail="Scheduled experiments rerun while this local service is active. Provider budgets apply separately to every run."
    >
      {error && (
        <div className="panel-body">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {schedules?.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Schedule</th>
                <th>Interval</th>
                <th>Next run</th>
                <th>Status</th>
                <th>Control</th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id}>
                  <td>
                    <a
                      className="table-primary"
                      href={`#/experiments/${encodeURIComponent(s.experiment_id)}`}
                    >
                      {s.name}
                    </a>
                    {s.last_error && <span className="table-secondary">{s.last_error}</span>}
                  </td>
                  <td>Every {s.interval_minutes} min</td>
                  <td>{s.enabled ? date(s.next_run_at) : '—'}</td>
                  <td>
                    <span className={`pill ${s.enabled ? 'green' : ''}`}>
                      {s.enabled ? 'Enabled' : 'Paused'}
                    </span>
                  </td>
                  <td>
                    <Button
                      variant="secondary"
                      busy={busy === s.id}
                      onClick={async () => {
                        setBusy(s.id);
                        setError('');
                        try {
                          await request(`/schedules/${encodeURIComponent(s.id)}`, {
                            method: 'PATCH',
                            body: JSON.stringify({ enabled: !s.enabled }),
                          });
                          setSchedules(await request<Schedule[]>('/schedules'));
                        } catch (e) {
                          setError(errorMessage(e));
                        } finally {
                          setBusy('');
                        }
                      }}
                    >
                      {s.enabled ? <Pause size={13} /> : <Play size={13} />}{' '}
                      {s.enabled ? 'Pause' : 'Enable'}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          compact
          title={schedules ? 'No recurring evaluations yet.' : 'Loading schedules…'}
          description="Open a completed experiment and choose Schedule to repeat the same evaluation at a defined interval."
        />
      )}
      <div className="table-footnote">
        <CalendarClock size={15} />
        Recurring runs preserve the original task cohort. New results may reflect changes in your
        endpoint; refreshed task collections require a new experiment.
      </div>
    </Panel>
  );
}
