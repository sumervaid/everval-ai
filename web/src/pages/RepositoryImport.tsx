import { useState } from 'react';
import { GitBranch, Plus, Search } from 'lucide-react';
import { post } from '../api';
import type { Collection } from '../types';
import { errorMessage } from '../format';
import { Button, Field, JsonView, Notice, Panel } from '../components';

interface MiningResult {
  collection: Collection | null;
  candidates: Record<string, unknown>[];
  counts: Record<string, number>;
}
export function RepositoryImport({ refresh }: { refresh: () => Promise<void> }) {
  const [path, setPath] = useState('');
  const [limit, setLimit] = useState('20');
  const [result, setResult] = useState<MiningResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  return (
    <div className="form-width">
      <Panel
        title="Inspect a local repository"
        detail="Mine conservative Python task candidates from local Git history. Imports never execute repository hooks or code on the host."
      >
        <form
          className="panel-body"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            setResult(null);
            try {
              setResult(
                await post<MiningResult>('/repositories/mine', {
                  path: path.trim(),
                  limit: Number(limit),
                }),
              );
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Notice>
            Use a repository you are authorized to evaluate. Candidates must include usable problem
            statements and checks. Review rejection evidence and validate admitted tasks before
            comparing systems.
          </Notice>
          <Field
            label="Absolute repository path on the service machine"
            required
            hint="The service reads the repository directly; this is not a path on your browser’s device."
          >
            <input
              required
              placeholder="/path/to/repository"
              value={path}
              onChange={(e) => setPath(e.target.value)}
            />
          </Field>
          <Field label="Recent changes to inspect">
            <input
              required
              type="number"
              min="1"
              max="100"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </Field>
          <Button type="submit" busy={busy}>
            <Search size={15} />
            Inspect repository history
          </Button>
        </form>
      </Panel>
      {error && <Notice tone="error">{error}</Notice>}
      {result && (
        <Panel
          title="Mining evidence"
          detail="Every candidate remains inspectable, including rejected changes."
        >
          <div className="panel-body">
            <div className="mining-counts">
              {Object.entries(result.counts).map(([label, count]) => (
                <div key={label}>
                  <span>{label.replaceAll('_', ' ')}</span>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
            {result.collection ? (
              <>
                <Notice tone="success">
                  <strong>{result.collection.scenarios.length} candidate scenarios</strong> are
                  ready for review in {result.collection.name}. Admission still requires collection
                  validation.
                </Notice>
                <Button
                  busy={saving}
                  onClick={async () => {
                    setSaving(true);
                    setError('');
                    try {
                      const saved = await post<Collection>('/collections', result.collection);
                      await refresh();
                      window.location.hash = `/collections/${encodeURIComponent(saved.id)}`;
                    } catch (e) {
                      setError(errorMessage(e));
                    } finally {
                      setSaving(false);
                    }
                  }}
                >
                  <Plus size={16} />
                  Save candidate collection
                </Button>
              </>
            ) : (
              <Notice tone="warning">
                No admissible task collection was produced. Inspect candidate reasons below.
                Repository history alone does not establish a valid evaluation task.
              </Notice>
            )}
            <div className="mining-candidates">
              {result.candidates.map((candidate, i) => (
                <JsonView
                  key={i}
                  value={candidate}
                  label={String(
                    candidate.subject ??
                      candidate.message ??
                      candidate.commit ??
                      `Candidate ${i + 1}`,
                  )}
                />
              ))}
            </div>
            <div className="principles-strip">
              <GitBranch size={16} />
              <span>No host-side code execution. Sandbox validation is a separate step.</span>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}
