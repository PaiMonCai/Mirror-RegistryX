import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileKey2,
  KeyRound,
  ListChecks,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  XCircle,
} from 'lucide-react';
import { Badge, Metric, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord, View, Mirror, Credential } from '../types';
import { cx, diagnosticMessage, formatMB, formatRate, hostFromImage } from '../utils';

export function Audit({ rows, reload }: any) {
  return <Panel title="审计日志" action={<button onClick={reload}><RefreshCw size={16} />刷新</button>}><table><thead><tr><th>时间</th><th>Actor</th><th>动作</th><th>资源</th><th>详情</th></tr></thead><tbody>{rows.map((row: AnyRecord) => <tr key={row.id}><td>{row.created_at}</td><td>{row.actor}</td><td>{row.action}</td><td>{row.resource_type}:{row.resource_id}</td><td><code>{JSON.stringify(row.detail)}</code></td></tr>)}</tbody></table></Panel>;
}
