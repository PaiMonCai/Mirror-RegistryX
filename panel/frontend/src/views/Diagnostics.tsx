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

export function Diagnostics({ diagnostics, reload }: any) {
  return <Panel title="诊断结果" action={<button onClick={reload}><RefreshCw size={16} />重新检查</button>}><div className="check-grid">{(diagnostics.checks || []).map((item: AnyRecord) => <div className="check" key={item.name}><div className="check-status"><Badge value={item.status} /></div><strong>{item.name}</strong><span className="breakable">{diagnosticMessage(item)}</span>{item.suggestion && <small className="breakable">{item.suggestion}</small>}</div>)}</div></Panel>;
}
