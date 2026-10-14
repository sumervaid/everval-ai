import { useState } from 'react';
import { post, request } from '../api';
import {
  Button,
  EmptyState,
  Field,
  Loading,
  Notice,
  PageHeader,
  Panel,
  Status,
} from '../components';
import { errorMessage } from '../format';
import type { Session, TeamUser } from '../team-types';
import { useRemote } from './useRemote';
import { SpendingLimits } from './SpendingLimits';

interface TokenInfo {
  id: string;
  user_id: string;
  label: string;
  expires_at: string;
  revoked_at: string | null;
}
export function TeamAccess({ session }: { session: Session }) {
  const users = useRemote<TeamUser[]>(session.team_enabled ? '/team/users' : null);
  const tokens = useRemote<TokenInfo[]>(session.team_enabled ? '/team/tokens' : null);
  const [name, setName] = useState('');
  const [role, setRole] = useState('reviewer');
  const [userId, setUserId] = useState('');
  const [label, setLabel] = useState('Browser review');
  const [days, setDays] = useState('30');
  const [secret, setSecret] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const reload = () => {
    users.reload();
    tokens.reload();
  };
  if (!session.team_enabled)
    return (
      <div className="team-stack">
        <PageHeader
          eyebrow="OPTIONAL TEAM ACCESS"
          title="Start with a usable administrator"
          description="The trusted local workspace remains usable until you deliberately enable individual authentication."
        />
        <Panel title="Enable from the local console">
          <div className="team-panel-body team-stack">
            <code>everval team bootstrap --name &quot;Your name&quot;</code>
            <p>
              This creates the first administrator and prints a personal token once. Save that token
              securely, then reconnect this browser with it. The shared workspace token stops
              granting access after team mode is enabled.
            </p>
            <Notice>
              For remote access, use an SSH tunnel or a correctly configured HTTPS service. These
              roles protect the HTTP API; anyone with direct workspace filesystem or CLI access
              remains a trusted owner.
            </Notice>
          </div>
        </Panel>
        <SpendingLimits />
      </div>
    );
  return (
    <div className="team-stack">
      <PageHeader
        eyebrow="LOCAL IDENTITIES"
        title="Team access"
        description="Personal tokens bind actions to identities. Reviewers receive assigned evidence; viewers receive decision summaries."
      />
      <Notice>
        Tokens are stored as salted hashes and shown once. Session tokens stay in this browser tab's
        session storage. Direct filesystem and CLI access remain trusted administrative access.
      </Notice>
      {(error || users.error || tokens.error) && (
        <Notice tone="error">{error || users.error || tokens.error}</Notice>
      )}
      {secret && (
        <Panel
          title="Copy this token now"
          detail="Closing this message removes the only browser copy of the newly issued credential."
        >
          <div className="team-panel-body team-stack">
            <Field label="New personal token">
              <textarea readOnly rows={3} value={secret} autoComplete="off" />
            </Field>
            <Button variant="secondary" onClick={() => setSecret('')}>
              I saved the token securely
            </Button>
          </div>
        </Panel>
      )}
      <SpendingLimits />
      <div className="team-form-grid">
        <Panel title="Create an identity">
          <form
            className="team-panel-body team-stack"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError('');
              setSecret('');
              try {
                const user = await post<TeamUser>('/team/users', { name, role });
                setName('');
                setUserId(user.id);
                reload();
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Display name">
              <input
                required
                maxLength={200}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            <Field label="Role">
              <select value={role} onChange={(event) => setRole(event.target.value)}>
                <option value="reviewer">Reviewer · assigned tasks only</option>
                <option value="viewer">Viewer · decision summaries</option>
                <option value="operator">Operator · evaluation workspace</option>
                <option value="admin">Administrator · workspace and team access</option>
              </select>
            </Field>
            <Button type="submit" busy={busy}>
              Create identity
            </Button>
          </form>
        </Panel>
        <Panel title="Issue a personal token">
          <form
            className="team-panel-body team-stack"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError('');
              setSecret('');
              try {
                const result = await post<{ token: string }>('/team/users/' + userId + '/tokens', {
                  label,
                  days: Number(days),
                });
                setSecret(result.token);
                tokens.reload();
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Identity">
              <select
                required
                value={userId}
                onChange={(event) => {
                  setUserId(event.target.value);
                  setSecret('');
                }}
              >
                <option value="">Choose an identity</option>
                {users.data
                  ?.filter((user) => user.active)
                  .map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.name} · {user.role}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Token label">
              <input required value={label} onChange={(event) => setLabel(event.target.value)} />
            </Field>
            <Field label="Expires in days">
              <input
                type="number"
                required
                min={1}
                max={365}
                value={days}
                onChange={(event) => setDays(event.target.value)}
              />
            </Field>
            <Button type="submit" busy={busy}>
              Issue token once
            </Button>
          </form>
        </Panel>
      </div>
      <Panel title="Identities">
        <div className="team-panel-body team-stack">
          {users.busy && <Loading />}
          {users.data?.map((user) => (
            <div className="team-account-row" key={user.id}>
              <div>
                <strong>{user.name}</strong>
                <p>
                  {user.role} · {user.active ? 'Active' : 'Disabled'}
                </p>
              </div>
              <Button
                variant="secondary"
                busy={busy}
                onClick={async () => {
                  setBusy(true);
                  setError('');
                  setSecret('');
                  try {
                    await request('/team/users/' + user.id, {
                      method: 'PATCH',
                      body: JSON.stringify({ active: !user.active }),
                    });
                    reload();
                  } catch (err) {
                    setError(errorMessage(err));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {user.active ? 'Disable identity' : 'Enable identity'}
              </Button>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Issued tokens">
        <div className="team-panel-body team-stack">
          {tokens.busy && <Loading />}
          {tokens.data?.length === 0 && (
            <EmptyState
              compact
              title="No tokens issued"
              description="Issue a personal credential for an active identity."
            />
          )}
          {tokens.data?.map((token) => (
            <div className="team-account-row" key={token.id}>
              <div>
                <strong>{token.label}</strong>
                <p>
                  {users.data?.find((user) => user.id === token.user_id)?.name ?? token.user_id} ·
                  Expires {new Date(token.expires_at).toLocaleDateString()}
                </p>
              </div>
              {token.revoked_at ? (
                <Status value="revoked" />
              ) : (
                <Button
                  variant="danger"
                  busy={busy}
                  onClick={async () => {
                    setBusy(true);
                    setError('');
                    setSecret('');
                    try {
                      await post('/team/tokens/' + token.id + '/revoke');
                      tokens.reload();
                    } catch (err) {
                      setError(errorMessage(err));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Revoke token
                </Button>
              )}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
