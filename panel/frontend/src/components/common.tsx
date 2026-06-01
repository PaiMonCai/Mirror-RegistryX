import type { ReactNode } from 'react';
import { cx } from '../utils';

export function Metric({ label, value }: { label: string; value: ReactNode }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

export function Panel({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return <section className="panel"><div className="panel-head"><h2>{title}</h2>{action}</div>{children}</section>;
}

export function Badge({ value }: { value: any }) {
  return <span className={cx('badge', String(value).toLowerCase())}>{String(value || '-')}</span>;
}
