import { useState } from 'react';
import { Button } from '../components';

/** Display only data received from the restricted review endpoint. */
export function EvidenceValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const [expanded, setExpanded] = useState(false);
  if (value === null || value === undefined) return <span className="muted">Not supplied</span>;
  if (typeof value === 'string') {
    const long = value.length > 2000;
    return (
      <div>
        <p className="evidence-prose">{long && !expanded ? value.slice(0, 2000) + '…' : value}</p>
        {long && (
          <Button variant="ghost" onClick={() => setExpanded(!expanded)}>
            {expanded ? 'Show less' : 'Read full response'}
          </Button>
        )}
      </div>
    );
  }
  if (typeof value !== 'object') return <span>{String(value)}</span>;
  if (depth > 5) return <pre className="evidence-code">{JSON.stringify(value, null, 2)}</pre>;
  const entries = Object.entries(value);
  if (!entries.length) return <p className="muted">No content was supplied.</p>;
  return (
    <dl className={'evidence-values ' + (Array.isArray(value) ? 'evidence-list' : '')}>
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{Array.isArray(value) ? Number(key) + 1 : key.replaceAll('_', ' ')}</dt>
          <dd>
            <EvidenceValue value={item} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
