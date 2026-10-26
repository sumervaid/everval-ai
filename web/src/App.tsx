import { Component, useCallback, useEffect, useState } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import {
  ArrowUpRight,
  BookOpen,
  ChevronRight,
  ChartNoAxesColumnIncreasing,
  FlaskConical,
  FolderClosed,
  LayoutDashboard,
  Menu,
  MessageCircle,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { ApiError, request, setToken } from './api';
import { errorMessage } from './format';
import type { Workspace } from './types';
import type { Session } from './team-types';
import { Button, Field, Loading, Notice } from './components';
import { Dashboard, SettingsPage } from './pages/Workspace';
import { CollectionsPage, CollectionDetail, NewCollection } from './pages/Collections';
import { ExperimentsPage, NewExperiment, ExperimentDetail } from './pages/Experiments';
import { SystemsPage } from './pages/Systems';
import { RankingsPage } from './pages/Rankings';
import { TrainingPage } from './pages/Training';
import { ChatPage } from './pages/Chat';
import {
  ReviewInbox,
  ReviewTask,
  ReviewQueueList,
  CreateReviewQueue,
  QueueBrief,
} from './pages/ReviewWorkbench';
import { ProductionImports, ProductionBatch } from './pages/ProductionCases';
import { TeamAccess } from './pages/TeamAccess';

const workspaceNav = [
  { path: '/', name: 'Overview', icon: LayoutDashboard },
  { path: '/chat', name: 'Chat', icon: MessageCircle },
  { path: '/collections', name: 'Collections', icon: FolderClosed },
  { path: '/experiments', name: 'Experiments', icon: FlaskConical },
  { path: '/rankings', name: 'Rankings', icon: ChartNoAxesColumnIncreasing },
  { path: '/systems', name: 'Systems', icon: SlidersHorizontal },
];
function currentPath() {
  return window.location.hash.slice(1) || '/';
}
class ErrorBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: '' };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {}
  render() {
    return this.state.error ? (
      <div className="fatal-error">
        <h1>The view could not be loaded.</h1>
        <p>Your saved workspace is unchanged. Reload to reconnect.</p>
        <Button onClick={() => location.reload()}>Reload workspace</Button>
      </div>
    ) : (
      this.props.children
    );
  }
}
export default function App() {
  return (
    <ErrorBoundary>
      <Workbench />
    </ErrorBoundary>
  );
}
function Workbench() {
  const [path, setPath] = useState(currentPath);
  const [session, setSession] = useState<Session | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState('');
  const [authRequired, setAuthRequired] = useState(false);
  const [token, updateToken] = useState('');
  const [authError, setAuthError] = useState('');
  const [authBusy, setAuthBusy] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [connected, setConnected] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const identity = await request<Session>('/session');
      setSession(identity);
      if (!identity.capabilities.includes('workspace.read')) {
        setWorkspace(null);
        setError('');
        setConnected(true);
        return;
      }
      const [overview, collections, systems, experiments, settings] = await Promise.all([
        request<Workspace['overview']>('/overview'),
        request<Workspace['collections']>('/collections'),
        request<Workspace['systems']>('/systems'),
        request<Workspace['experiments']>('/experiments'),
        request<Workspace['settings']>('/settings'),
      ]);
      setWorkspace({ overview, collections, systems, experiments, settings });
      setError('');
      setConnected(true);
    } catch (err) {
      setConnected(false);
      if (err instanceof ApiError && err.status === 401) {
        setWorkspace(null);
        setSession(null);
      }
      if (!(err instanceof ApiError && err.status === 401)) setError(errorMessage(err));
    }
  }, []);
  useEffect(() => {
    const navigate = () => {
      setPath(currentPath());
      setMobileNav(false);
      window.scrollTo({ top: 0 });
    };
    const requireAuth = () => {
      setAuthRequired(true);
      setWorkspace(null);
      setSession(null);
    };
    window.addEventListener('hashchange', navigate);
    window.addEventListener('workspace:auth-required', requireAuth);
    void refresh();
    return () => {
      window.removeEventListener('hashchange', navigate);
      window.removeEventListener('workspace:auth-required', requireAuth);
    };
  }, [refresh]);
  useEffect(() => {
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, 12000);
    return () => clearInterval(interval);
  }, [refresh]);
  useEffect(() => {
    document.title = `${path.startsWith('/chat') ? 'Chat' : path.startsWith('/collections') ? 'Collections' : path.startsWith('/experiments') ? 'Experiments' : path.startsWith('/rankings') ? 'Rankings' : path.startsWith('/training') ? 'Training' : path.startsWith('/systems') ? 'Systems' : path.startsWith('/reviews') ? 'Review desk' : path.startsWith('/cases') ? 'Production examples' : path.startsWith('/team') ? 'Team access' : path.startsWith('/settings') ? 'Settings' : 'Overview'} · Everval AI`;
  }, [path]);
  useEffect(() => {
    if (!authRequired) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const background = [...document.querySelectorAll<HTMLElement>('.main-shell, .sidebar')];
    background.forEach((element) => {
      element.inert = true;
    });
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setAuthRequired(false);
        return;
      }
      if (event.key !== 'Tab') return;
      const controls = [
        ...document.querySelectorAll<HTMLElement>(
          '.auth-modal input, .auth-modal button:not(:disabled)',
        ),
      ];
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      background.forEach((element) => {
        element.inert = false;
      });
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [authRequired]);
  const canManage = session?.capabilities.includes('workspace.write') ?? false;
  const canReadWorkspace = session?.capabilities.includes('workspace.read') ?? false;
  const canReview = session?.capabilities.includes('reviews.assigned') ?? false;
  const canSummarize = session?.capabilities.includes('summary.read') ?? false;
  const nav = [
    ...(canManage ? workspaceNav : []),
    ...(canReadWorkspace
      ? [{ path: '/training', name: 'Training', icon: ChartNoAxesColumnIncreasing }]
      : []),
    ...(canReview ? [{ path: '/reviews', name: 'Review desk', icon: ShieldCheck }] : []),
    ...(!canReview && canSummarize
      ? [{ path: '/reviews/queues', name: 'Decision briefs', icon: ShieldCheck }]
      : []),
    ...(canManage ? [{ path: '/cases', name: 'Production examples', icon: FolderClosed }] : []),
    ...(session?.capabilities.includes('team.admin')
      ? [{ path: '/team', name: 'Team access', icon: Settings2 }]
      : []),
  ];
  const route = path.split('?')[0];
  const selectedNav = nav.find((item) => item.path !== '/' && path.startsWith(item.path)) ??
    nav[0] ?? { path: '/', name: 'Workspace', icon: LayoutDashboard };
  let content: ReactNode = <Loading />;
  if (workspace) {
    if (route === '/') content = <Dashboard workspace={workspace} refresh={refresh} />;
    else if (route === '/chat' && canManage) content = <ChatPage />;
    else if (route === '/collections')
      content = <CollectionsPage workspace={workspace} refresh={refresh} />;
    else if (route === '/collections/new') content = <NewCollection refresh={refresh} />;
    else if (route.startsWith('/collections/'))
      content = (
        <CollectionDetail
          key={path}
          id={decodeURIComponent(route.slice('/collections/'.length))}
          refresh={refresh}
        />
      );
    else if (route === '/experiments')
      content = <ExperimentsPage workspace={workspace} refresh={refresh} />;
    else if (route === '/experiments/new')
      content = <NewExperiment workspace={workspace} refresh={refresh} />;
    else if (route.startsWith('/experiments/'))
      content = (
        <ExperimentDetail
          key={path}
          id={decodeURIComponent(route.slice('/experiments/'.length))}
          refresh={refresh}
        />
      );
    else if (route === '/rankings') content = <RankingsPage />;
    else if (route === '/systems')
      content = <SystemsPage workspace={workspace} refresh={refresh} />;
    else if (route === '/settings') content = <SettingsPage workspace={workspace} />;
    else
      content = (
        <Notice>
          This page does not exist. <a href="#/">Return to the overview.</a>
        </Notice>
      );
  }
  if (session) {
    if (route === '/training' && canReadWorkspace) content = <TrainingPage />;
    else if ((route === '/reviews' || (route === '/' && !canManage)) && canReview)
      content = <ReviewInbox key={session.id} session={session} />;
    else if ((route === '/reviews/queues' || (route === '/' && !canManage)) && canSummarize)
      content = <ReviewQueueList canManage={canManage} />;
    else if (route === '/reviews/queues/new' && workspace && canManage)
      content = <CreateReviewQueue workspace={workspace} session={session} />;
    else if (route.startsWith('/reviews/queues/') && canSummarize)
      content = (
        <QueueBrief
          key={path}
          id={decodeURIComponent(route.slice('/reviews/queues/'.length))}
          canManage={canManage}
        />
      );
    else if (route.startsWith('/reviews/items/') && canReview)
      content = (
        <ReviewTask
          key={path + session.id}
          id={decodeURIComponent(route.slice('/reviews/items/'.length))}
        />
      );
    else if (route === '/cases' && canManage) content = <ProductionImports />;
    else if (route.startsWith('/cases/') && canManage)
      content = (
        <ProductionBatch
          key={path}
          id={decodeURIComponent(route.slice('/cases/'.length))}
          refresh={refresh}
        />
      );
    else if (route === '/team' && session.capabilities.includes('team.admin'))
      content = <TeamAccess session={session} />;
    else if (!canManage)
      content = (
        <Notice>
          This page is unavailable for your role. <a href="#/">Open your workspace.</a>
        </Notice>
      );
  }
  return (
    <div className="app-shell">
      <a
        href="#main"
        className="skip-link"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById('main')?.focus();
        }}
      >
        Skip to content
      </a>
      {mobileNav && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setMobileNav(false)}
        />
      )}
      <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
        <a href="#/" className="brand" aria-label="Everval AI overview">
          <span className="brand-mark">
            v<span>_</span>
          </span>
          <span>
            Everval AI<span className="brand-subtitle">EVALUATION WORKBENCH</span>
          </span>
        </a>
        <div className="workspace-switch">
          <div className="workspace-avatar">W</div>
          <div>
            <strong>{session?.name ?? 'Local workspace'}</strong>
            <span>
              {session?.mode === 'team'
                ? session.role + ' · personal access'
                : 'Your evidence, on your terms'}
            </span>
          </div>
        </div>
        <div className="nav-caption">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {nav.map((item) => (
            <a
              key={item.path}
              href={`#${item.path}`}
              className={selectedNav.path === item.path && path !== '/settings' ? 'active' : ''}
              aria-current={
                selectedNav.path === item.path && path !== '/settings' ? 'page' : undefined
              }
            >
              <item.icon size={18} />
              {item.name}
              {item.path === '/collections' && !!workspace?.collections.length && (
                <span className="nav-count">{workspace.collections.length}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="sidebar-bottom">
          {canManage && (
            <a href="#/settings" className={path === '/settings' ? 'active' : ''}>
              <Settings2 size={17} />
              Settings & setup
            </a>
          )}
          <a href="https://github.com/sumervaid/everval-ai" target="_blank" rel="noreferrer">
            <BookOpen size={17} />
            Documentation
            <ArrowUpRight size={13} className="end-icon" />
          </a>
          <div className="local-status">
            <ShieldCheck size={17} />
            <div>
              <strong>Locally stored</strong>
              <span>No telemetry. Your workspace.</span>
            </div>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <div className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              onClick={() => setMobileNav(true)}
              aria-label="Open navigation"
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{path === '/settings' ? 'Settings' : selectedNav.name}</strong>
          </div>
          <div className="connection">
            {session?.mode !== 'local' && session && (
              <button
                className="inline-button"
                onClick={() => {
                  setToken('');
                  updateToken('');
                  setWorkspace(null);
                  setSession(null);
                  setAuthRequired(true);
                }}
              >
                Sign out
              </button>
            )}
            <span className={`connection-dot ${connected ? 'connected' : ''}`} />
            {connected ? 'Local service connected' : 'Connecting to local service'}
            <span className="version">
              {workspace?.settings.version ? `v${workspace.settings.version}` : 'LOCAL'}
            </span>
          </div>
        </div>
        <main id="main" tabIndex={-1}>
          {error && (
            <div className="workspace-error">
              <Notice tone="error">
                {error}{' '}
                <button className="inline-button" onClick={() => void refresh()}>
                  Reconnect
                </button>
              </Notice>
            </div>
          )}
          <div key={session?.id ?? 'signed-out'}>{content}</div>
        </main>
        <footer className="app-footer">
          <span>Everval AI</span>
          <span>Decisions grounded in evidence.</span>
          <span>Local workspace</span>
        </footer>
      </div>
      {authRequired && (
        <div className="modal-backdrop">
          <section
            className="modal auth-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="auth-title"
          >
            <div className="modal-icon">
              <ShieldCheck size={24} />
            </div>
            <h2 id="auth-title">Unlock your workspace</h2>
            <p>
              Enter your personal access token, or the workspace token for a legacy service. It
              stays in this browser session.
            </p>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setAuthBusy(true);
                setAuthError('');
                try {
                  setToken(token);
                  await request('/session');
                  updateToken('');
                  setAuthRequired(false);
                  await refresh();
                } catch (err) {
                  setToken('');
                  setAuthError(errorMessage(err));
                } finally {
                  setAuthBusy(false);
                }
              }}
            >
              <Field label="Access token">
                <input
                  autoFocus
                  type="password"
                  autoComplete="off"
                  required
                  value={token}
                  onChange={(e) => updateToken(e.target.value)}
                />
              </Field>
              {authError && <Notice tone="error">{authError}</Notice>}
              <Button type="submit" busy={authBusy}>
                Connect to workspace
              </Button>
            </form>
            <button
              className="icon-button modal-close"
              aria-label="Close token dialog"
              onClick={() => setAuthRequired(false)}
            >
              <X size={18} />
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
