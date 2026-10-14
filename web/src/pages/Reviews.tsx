import { useEffect, useState } from 'react';
import { MessageSquarePlus, Plus } from 'lucide-react';
import { post, request } from '../api';
import { date, errorMessage, percent } from '../format';
import { Button, Field, Notice } from '../components';

interface HumanReview {
  id: string;
  trial_id: string;
  reviewer: string;
  dimension: string;
  score: number;
  rationale: string;
  created_at: string;
  latest?: boolean;
}
export function TrialReviews({ experimentId, trialId }: { experimentId: string; trialId: string }) {
  const [reviews, setReviews] = useState<HumanReview[]>([]);
  const [adding, setAdding] = useState(false);
  const [reviewer, setReviewer] = useState('');
  const [dimension, setDimension] = useState('quality');
  const [score, setScore] = useState('1');
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const endpoint = `/experiments/${encodeURIComponent(experimentId)}/reviews`;
  useEffect(() => {
    const controller = new AbortController();
    request<HumanReview[]>(endpoint, { signal: controller.signal })
      .then(setReviews)
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      });
    return () => controller.abort();
  }, [endpoint]);
  const relevant = reviews.filter((r) => r.trial_id === trialId);
  return (
    <section className="review-panel">
      <div className="review-heading">
        <div>
          <MessageSquarePlus size={16} />
          <h3>Human review</h3>
        </div>
        <Button variant="secondary" onClick={() => setAdding(!adding)}>
          <Plus size={13} />
          {adding ? 'Close form' : 'Add review'}
        </Button>
      </div>
      <p className="help-text">
        Human judgments remain separate from automated checks. Reviewer names are supplied labels,
        not verified identities.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      {relevant.length > 0 && (
        <div className="review-history">
          {relevant.map((r) => (
            <article key={r.id} className="review-entry">
              <div>
                <strong>{r.reviewer}</strong>
                <span className="pill">{r.dimension}</span>
                {r.latest === false && <span className="pill">Earlier judgment</span>}
                <span>{percent(r.score)}</span>
              </div>
              <p>{r.rationale}</p>
              <time>{date(r.created_at)}</time>
            </article>
          ))}
        </div>
      )}
      {!relevant.length && !adding && (
        <p className="small-note">No human judgments recorded for this trial.</p>
      )}
      {adding && (
        <form
          className="review-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            try {
              await post(endpoint, {
                trial_id: trialId,
                reviewer: reviewer.trim(),
                dimension: dimension.trim(),
                score: Number(score),
                rationale: rationale.trim(),
              });
              setReviews(await request<HumanReview[]>(endpoint));
              setAdding(false);
              setRationale('');
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="form-grid">
            <Field label="Reviewer label" required>
              <input
                required
                maxLength={160}
                placeholder="e.g. Operations reviewer"
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
              />
            </Field>
            <Field label="Dimension" required>
              <input
                required
                maxLength={80}
                placeholder="e.g. completeness"
                value={dimension}
                onChange={(e) => setDimension(e.target.value)}
              />
            </Field>
          </div>
          <div className="form-grid">
            <Field label="Score · 0 to 1" required>
              <input
                required
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={score}
                onChange={(e) => setScore(e.target.value)}
              />
            </Field>
            <div />
          </div>
          <Field
            label="Rationale"
            required
            hint="Explain the judgment with specific evidence from this trial."
          >
            <textarea
              required
              rows={3}
              maxLength={10000}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
            />
          </Field>
          <Button type="submit" busy={busy}>
            Record review
          </Button>
        </form>
      )}
    </section>
  );
}
