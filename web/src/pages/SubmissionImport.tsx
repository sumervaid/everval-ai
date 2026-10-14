import { useRef, useState } from 'react';
import { FileUp, Upload, X } from 'lucide-react';
import { post } from '../api';
import type { Experiment } from '../types';
import { errorMessage, parseObject } from '../format';
import { Button, Field, Notice, Panel } from '../components';

export function SubmissionImport({
  refresh,
  onClose,
}: {
  refresh: () => Promise<void>;
  onClose: () => void;
}) {
  const [body, setBody] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  async function submit(value: string) {
    setBusy(true);
    setError('');
    try {
      const data = parseObject(value, 'Submission bundle');
      const experiment = await post<Experiment>('/submissions', data);
      await refresh();
      window.location.hash = `/experiments/${encodeURIComponent(experiment.id)}`;
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel
      title="Grade externally produced outputs"
      detail="Bring results from an agent, application, or workflow runner. The local service applies the collection’s checks."
      action={
        <button className="icon-button" aria-label="Close result import" onClick={onClose}>
          <X size={18} />
        </button>
      }
    >
      <div className="panel-body">
        <Notice>
          First download task inputs from a collection. Run your system against those public inputs,
          then supply the matching collection hash and scenario IDs with each output. Imported usage
          and timing are treated as supplied evidence.
        </Notice>
        {error && <Notice tone="error">{error}</Notice>}
        <input
          ref={fileInput}
          type="file"
          accept=".json"
          className="visually-hidden"
          aria-label="Submission bundle file"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            if (file.size > 5 * 1024 * 1024) {
              setError('Submission bundles must be smaller than 5 MB.');
              return;
            }
            setBody(await file.text());
          }}
        />
        <Button variant="secondary" onClick={() => fileInput.current?.click()}>
          <FileUp size={16} />
          Read JSON file
        </Button>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit(body);
          }}
        >
          <Field
            label="Submission bundle · JSON"
            hint="The collection hash binds results to the exact task collection."
          >
            <textarea
              className="code-input"
              rows={13}
              required
              style={{ marginTop: 15 }}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder={
                '{\n  "name": "External agent evaluation",\n  "collection_id": "collection-id",\n  "collection_hash": "hash-from-task-inputs",\n  "submissions": {\n    "scenario-id": {\n      "output": { "answer": "your result" },\n      "cost_usd": 0.01\n    }\n  }\n}'
              }
            />
          </Field>
          <div className="form-footer">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" busy={busy}>
              <Upload size={16} />
              Import & grade outputs
            </Button>
          </div>
        </form>
      </div>
    </Panel>
  );
}
