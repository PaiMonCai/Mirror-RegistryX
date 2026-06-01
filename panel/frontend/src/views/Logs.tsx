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

export function Logs({ logs, events, reload }: any) {
  return <div className="stack"><Panel title="文本日志" action={<button onClick={reload}><RefreshCw size={16} />刷新</button>}><pre>{logs}</pre></Panel><Panel title="事件"><table><tbody>{events.map((e: AnyRecord) => <tr key={e.id}><td>{e.created_at}</td><td><Badge value={e.level} /></td><td>{e.message}</td></tr>)}</tbody></table></Panel></div>;
}
