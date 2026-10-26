import { useEffect, useRef, useState } from 'react';
import { ArrowUp, CalendarClock, Download, MessageCircle, Plus } from 'lucide-react';
import { getToken, post, request } from '../api';
import { Button, Notice, PageHeader } from '../components';
import { errorMessage } from '../format';

interface Catalog {
  systems: { id: string; name: string; model: string; kind: string }[];
  collections: { id: string; name: string; scenario_count?: number }[];
}
export interface Conversation {
  id: string;
  status: string;
  ready: boolean;
  missing: string[];
  messages: { role: string; content: string }[];
  draft: {
    name?: string;
    collection_id?: string;
    system_ids?: string[];
    interval_minutes?: number;
    report_formats?: string[];
    budget_usd?: number;
    lifetime_budget_usd?: number;
  };
  experiment_id?: string | null;
  schedule_id?: string | null;
  schedule?: { last_error?: string | null };
  report_errors?: { experiment_id: string; error: string }[];
}
interface Report {
  id: string;
  experiment_id: string;
  format: string;
  created_at: string;
}
function frequency(minutes?: number) {
  if (!minutes) return 'Choose a schedule';
  if (minutes % 10080 === 0) return `Every ${minutes / 10080} week${minutes === 10080 ? '' : 's'}`;
  if (minutes % 1440 === 0) return `Every ${minutes / 1440} day${minutes === 1440 ? '' : 's'}`;
  if (minutes % 60 === 0) return `Every ${minutes / 60} hour${minutes === 60 ? '' : 's'}`;
  return `Every ${minutes} minutes`;
}
const money = (value?: number) =>
  value === undefined || value === null ? 'Not set' : `$${value.toFixed(2)}`;

export function ChatPage() {
  const [catalog, setCatalog] = useState<Catalog>({ systems: [], collections: [] });
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState('');
  const generation = useRef(0);
  const sending = useRef(false);
  const transcript = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      request<Catalog>('/conversations/catalog', { signal: controller.signal }),
      request<Conversation[]>('/conversations', { signal: controller.signal }),
    ])
      .then(([available, history]) => {
        if (!controller.signal.aborted) {
          setCatalog(available);
          setSessions(history);
          setLoaded(true);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorMessage(err));
      });
    return () => {
      controller.abort();
      generation.current++;
    };
  }, []);

  useEffect(() => {
    const element = transcript.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [current?.messages.length]);

  useEffect(() => {
    const element = transcript.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      element.scrollTop = element.scrollHeight;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!current?.id) return;
    const id = current.id;
    const controller = new AbortController();
    async function poll() {
      if (sending.current) return;
      const expected = generation.current;
      try {
        const [value, files] = await Promise.all([
          request<Conversation>(`/conversations/${encodeURIComponent(id)}`, {
            signal: controller.signal,
          }),
          request<Report[]>(`/conversations/${encodeURIComponent(id)}/reports`, {
            signal: controller.signal,
          }),
        ]);
        if (!controller.signal.aborted && expected === generation.current && !sending.current) {
          setCurrent(value);
          setReports(files);
        }
      } catch (err) {
        if (!controller.signal.aborted && expected === generation.current)
          setError(errorMessage(err));
      }
    }
    void poll();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') void poll();
    }, 4000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [current?.id]);

  async function open(id: string) {
    if (sending.current) return;
    sending.current = true;
    const expected = ++generation.current;
    setBusy(true);
    setError('');
    setReports([]);
    try {
      const [value, files] = await Promise.all([
        request<Conversation>(`/conversations/${encodeURIComponent(id)}`),
        request<Report[]>(`/conversations/${encodeURIComponent(id)}/reports`),
      ]);
      if (expected === generation.current) {
        setCurrent(value);
        setReports(files);
        setInput('');
      }
    } catch (err) {
      if (expected === generation.current) setError(errorMessage(err));
    } finally {
      sending.current = false;
      if (expected === generation.current) setBusy(false);
    }
  }

  async function send(text = input) {
    const message = text.trim();
    if (!message || sending.current) return;
    sending.current = true;
    setBusy(true);
    setError('');
    const expected = ++generation.current;
    try {
      const session = current ?? (await post<Conversation>('/conversations'));
      if (expected !== generation.current) return;
      setCurrent(session);
      const value = await post<Conversation>(
        `/conversations/${encodeURIComponent(session.id)}/messages`,
        { message },
      );
      if (expected !== generation.current) return;
      setCurrent(value);
      setInput('');
      setSessions((items) => [value, ...items.filter((item) => item.id !== value.id)]);
      setReports(
        await request<Report[]>(`/conversations/${encodeURIComponent(session.id)}/reports`),
      );
      composer.current?.focus();
    } catch (err) {
      if (expected === generation.current) setError(errorMessage(err));
    } finally {
      sending.current = false;
      if (expected === generation.current) setBusy(false);
    }
  }

  async function download(item: Report) {
    if (!current) return;
    setDownloadBusy(item.id);
    setError('');
    try {
      const headers = new Headers();
      const token = getToken();
      if (token) headers.set('Authorization', `Bearer ${token}`);
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(current.id)}/reports/${encodeURIComponent(item.id)}`,
        { headers, credentials: 'same-origin' },
      );
      if (!response.ok) {
        if (response.status === 401) window.dispatchEvent(new Event('workspace:auth-required'));
        throw new Error('The report could not be downloaded. Try again.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `everval-${item.experiment_id}.${item.format === 'markdown' ? 'md' : item.format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setDownloadBusy('');
    }
  }

  const draft = current?.draft;
  const availableCollection = catalog.collections.find((item) => item.id === draft?.collection_id);
  const selectedSystems = catalog.systems.filter((item) => draft?.system_ids?.includes(item.id));
  const fixtureSystems = catalog.systems.filter((item) =>
    ['reference', 'incomplete'].includes(item.kind),
  );
  const sample =
    catalog.collections.length && fixtureSystems.length
      ? `Compare ${fixtureSystems.map((item) => '"' + item.name + '"').join(' and ')} on "${catalog.collections[0].name}" every 2 weeks and save HTML and CSV reports. Budget $0 per run and $0 total.`
      : 'Compare Support baseline and Support candidate on Support tickets every 2 weeks. Save HTML and CSV reports. Budget $2 per run and $20 total.';

  return (
    <>
      <PageHeader
        eyebrow="SET IT UP IN A CONVERSATION"
        title="What would you like to benchmark?"
        description="Choose the models, the work, and how often. Everval runs the comparison and keeps your reports here."
        actions={
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => {
              generation.current++;
              setCurrent(null);
              setReports([]);
              setInput('');
              setError('');
              composer.current?.focus();
            }}
          >
            <Plus size={15} /> New conversation
          </Button>
        }
      />
      {error && <Notice tone="error">{error}</Notice>}
      {current?.schedule?.last_error && (
        <Notice tone="warning">Scheduled runs are paused: {current.schedule.last_error}</Notice>
      )}
      {current?.report_errors?.map((issue) => (
        <Notice key={issue.experiment_id} tone="warning">
          A report could not be saved. The worker will retry. {issue.error}
        </Notice>
      ))}
      <div className="benchmark-chat-layout">
        <section className="benchmark-chat-main" aria-label="Benchmark conversation">
          <div className="benchmark-transcript" role="log" aria-live="polite" ref={transcript}>
            {!current?.messages.length ? (
              <div className="benchmark-welcome">
                <div className="benchmark-chat-mark">
                  <MessageCircle size={26} />
                </div>
                <h2>A recurring benchmark, in your own words.</h2>
                <p>
                  Tell me which saved models and task set to compare. Include a schedule, report
                  format, and spending limits.
                </p>
                <button
                  className="benchmark-example"
                  disabled={!loaded || busy}
                  onClick={() => {
                    setInput(sample);
                    composer.current?.focus();
                  }}
                >
                  <span>TRY A REQUEST</span>
                  {sample}
                  <small>Places this example in the message box.</small>
                </button>
              </div>
            ) : (
              current.messages.map((message, index) => (
                <article
                  className={`benchmark-message ${message.role === 'user' ? 'from-user' : 'from-everval'}`}
                  key={index}
                >
                  <span>{message.role === 'user' ? 'You' : 'Everval'}</span>
                  <p>{message.content}</p>
                </article>
              ))
            )}
            {busy && (
              <p className="benchmark-chat-thinking" role="status">
                Working on your request…
              </p>
            )}
          </div>
          {current?.experiment_id && (
            <div className="benchmark-chat-actions">
              <a
                className="button secondary"
                href={`#/experiments/${encodeURIComponent(current.experiment_id)}`}
              >
                View results
              </a>
              <Button variant="ghost" busy={busy} onClick={() => void send('status')}>
                Status
              </Button>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => void send(current.status === 'paused' ? 'resume' : 'pause')}
              >
                {current.status === 'paused' ? 'Resume' : 'Pause future runs'}
              </Button>
            </div>
          )}
          <form
            className="benchmark-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <label htmlFor="benchmark-message" className="sr-only">
              Your benchmark request
            </label>
            <textarea
              id="benchmark-message"
              ref={composer}
              value={input}
              maxLength={8000}
              rows={3}
              placeholder="Compare these models on these tasks every 2 weeks…"
              disabled={busy || !loaded}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            <div>
              <small>Enter to send · Shift+Enter for a new line</small>
              <Button type="submit" busy={busy} disabled={!input.trim() || !loaded}>
                <ArrowUp size={16} /> Send
              </Button>
            </div>
            <p className="benchmark-composer-note">
              Complete requests start automatically. Reports stay in this workspace; the worker must
              remain running.
            </p>
          </form>
        </section>
        <aside className="benchmark-chat-sidebar" aria-label="Benchmark setup">
          <section className="panel benchmark-plan">
            <div className="benchmark-plan-heading">
              <CalendarClock size={18} />
              <h2>{draft ? 'Your benchmark' : 'Ready to connect'}</h2>
              {current && <span className="pill">{current.status}</span>}
            </div>
            <dl>
              <dt>Models</dt>
              <dd>
                {selectedSystems.map((item) => item.name).join(', ') ||
                  'Choose from your saved models'}
              </dd>
              <dt>Tasks and data</dt>
              <dd>{availableCollection?.name || 'Choose a saved task set'}</dd>
              <dt>Schedule</dt>
              <dd>{frequency(draft?.interval_minutes)}</dd>
              <dt>Reports</dt>
              <dd>
                {draft?.report_formats?.map((item) => item.toUpperCase()).join(' + ') ||
                  'HTML, CSV, JSON, or Markdown'}
              </dd>
              <dt>Spending limits</dt>
              <dd>
                {draft
                  ? `${money(draft.budget_usd)} per run · ${money(draft.lifetime_budget_usd)} total`
                  : 'You set the per-run and total caps'}
              </dd>
            </dl>
            {current?.missing?.length ? (
              <p className="benchmark-next-detail">
                Answer the follow-up in the conversation to finish setup.
              </p>
            ) : null}
          </section>
          {reports.length > 0 && (
            <section className="panel benchmark-plan">
              <h2>Your reports</h2>
              <div className="benchmark-report-list">
                {reports.map((item) => (
                  <Button
                    key={item.id}
                    variant="secondary"
                    busy={downloadBusy === item.id}
                    onClick={() => void download(item)}
                  >
                    <Download size={14} /> {item.format.toUpperCase()} ·{' '}
                    {new Date(item.created_at).toLocaleDateString()}
                  </Button>
                ))}
              </div>
            </section>
          )}
          <details className="panel benchmark-plan" open={!current}>
            <summary>Available in this workspace</summary>
            <h3>Models</h3>
            {catalog.systems.length ? (
              <ul>
                {catalog.systems.map((item) => (
                  <li key={item.id}>
                    {item.name}
                    {['reference', 'incomplete'].includes(item.kind) ? ' · demo control' : ''}
                  </li>
                ))}
              </ul>
            ) : (
              <p>No models saved yet.</p>
            )}
            <a className="text-link" href="#/systems">
              Manage models →
            </a>
            <h3>Task sets</h3>
            {catalog.collections.length ? (
              <ul>
                {catalog.collections.map((item) => (
                  <li key={item.id}>{item.name}</li>
                ))}
              </ul>
            ) : (
              <p>No task sets saved yet.</p>
            )}
            <a className="text-link" href="#/collections/new">
              Add tasks and data →
            </a>
          </details>
          {sessions.length > 0 && (
            <section className="panel benchmark-plan">
              <h2>Recent conversations</h2>
              <div className="benchmark-history">
                {sessions.map((item) => (
                  <button
                    key={item.id}
                    disabled={busy}
                    aria-current={item.id === current?.id ? 'true' : undefined}
                    onClick={() => void open(item.id)}
                  >
                    {item.draft?.name || 'Benchmark setup'}
                    <small>{item.status}</small>
                  </button>
                ))}
              </div>
            </section>
          )}
        </aside>
      </div>
    </>
  );
}
