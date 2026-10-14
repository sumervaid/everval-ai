import { Children, cloneElement, isValidElement, useId, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import {
  ArrowRight,
  Check,
  ChevronDown,
  CircleAlert,
  LoaderCircle,
  X,
  PackageOpen,
} from 'lucide-react';
import { domainLabel, json } from './format';

export function Button({
  children,
  variant = 'primary',
  busy = false,
  className = '',
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      className={`button ${variant} ${className}`}
    >
      {busy && <LoaderCircle size={15} className="spin" />}
      {children}
    </button>
  );
}
export function LinkButton({
  href,
  children,
  secondary = false,
}: {
  href: string;
  children: ReactNode;
  secondary?: boolean;
}) {
  return (
    <a className={`button ${secondary ? 'secondary' : 'primary'}`} href={href}>
      {children}
    </a>
  );
}
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="header-actions">{actions}</div>}
    </header>
  );
}
export function Panel({
  children,
  title,
  detail,
  action,
  className = '',
}: {
  children: ReactNode;
  title?: string;
  detail?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-heading">
          <div>
            <h2>{title}</h2>
            {detail && <p>{detail}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Notice({
  children,
  tone = 'info',
}: {
  children: ReactNode;
  tone?: 'info' | 'success' | 'warning' | 'error';
}) {
  return (
    <div className={`notice ${tone}`} role={tone === 'error' ? 'alert' : undefined}>
      {tone === 'success' ? <Check size={17} /> : <CircleAlert size={17} />}
      <div>{children}</div>
    </div>
  );
}
export function EmptyState({
  title,
  description,
  action,
  compact = false,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={`empty-state ${compact ? 'compact' : ''}`}>
      <div className="empty-icon">
        <PackageOpen size={25} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Status({ value }: { value: string }) {
  return (
    <span className={`status ${value.replace(/[^a-z_-]/gi, '')}`}>
      <span className={`status-dot ${value === 'running' ? 'pulse' : ''}`} />
      {value.replaceAll('_', ' ')}
    </span>
  );
}
export function DomainBadge({ domain }: { domain: string }) {
  return <span className={`domain-badge ${domain}`}>{domainLabel[domain] ?? domain}</span>;
}
export function Field({
  label,
  children,
  hint,
  required,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  required?: boolean;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const controls = (nodes: ReactNode): ReactNode =>
    Children.map(nodes, (node) => {
      if (!isValidElement(node)) return node;
      const element = node as ReactElement<{
        children?: ReactNode;
        id?: string;
        'aria-describedby'?: string;
      }>;
      if (
        typeof element.type === 'string' &&
        ['input', 'select', 'textarea'].includes(element.type)
      ) {
        return cloneElement(element, { id, ...(hint ? { 'aria-describedby': hintId } : {}) });
      }
      return element.props.children
        ? cloneElement(element, { children: controls(element.props.children) })
        : element;
    });
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
        {required && (
          <span className="required" aria-hidden="true">
            {' '}
            *
          </span>
        )}
      </label>
      {controls(children)}
      {hint && (
        <span className="field-hint" id={hintId}>
          {hint}
        </span>
      )}
    </div>
  );
}

export function JsonView({
  value,
  label = 'View data',
  open = false,
}: {
  value: unknown;
  label?: string;
  open?: boolean;
}) {
  return (
    <details className="json-view" open={open}>
      <summary>
        {label}
        <ChevronDown size={15} />
      </summary>
      <pre>{json(value)}</pre>
    </details>
  );
}
export function Loading({ text = 'Loading workspace' }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={24} className="spin" />
      <span>{text}…</span>
    </div>
  );
}
export function TextLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} className="text-link">
      {children}
      <ArrowRight size={14} />
    </a>
  );
}
export function ConfirmDelete({
  name,
  onConfirm,
}: {
  name: string;
  onConfirm: () => Promise<void>;
}) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!confirm)
    return (
      <Button variant="ghost" onClick={() => setConfirm(true)}>
        Delete
      </Button>
    );
  return (
    <div className="confirm-inline">
      <span>Delete {name}?</span>
      <Button
        variant="danger"
        busy={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await onConfirm();
          } catch (e) {
            setError(String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        Delete
      </Button>
      <button className="icon-button" aria-label="Keep item" onClick={() => setConfirm(false)}>
        <X size={16} />
      </button>
      {error && <span role="alert">{error}</span>}
    </div>
  );
}
