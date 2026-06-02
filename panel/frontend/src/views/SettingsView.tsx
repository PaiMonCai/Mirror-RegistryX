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

export function SettingsView({ settings, api, reload, notify }: any) {
  const [form, setForm] = useState<AnyRecord>({});
  useEffect(() => setForm(settings || {}), [settings]);
  return <Panel title="同步与飞书通知"><div className="form-grid"><input type="number" value={form.check_interval_minutes || ''} onChange={(e) => setForm({ ...form, check_interval_minutes: Number(e.target.value) })} placeholder="同步间隔分钟" /><input type="number" value={form.sync_concurrency || ''} onChange={(e) => setForm({ ...form, sync_concurrency: Number(e.target.value) })} placeholder="并发" /><input type="number" value={form.sync_retry_count || ''} onChange={(e) => setForm({ ...form, sync_retry_count: Number(e.target.value) })} placeholder="重试" /><input value={form.notify_webhook_url || ''} onChange={(e) => setForm({ ...form, notify_webhook_url: e.target.value })} placeholder="飞书 Webhook URL" aria-label="飞书 Webhook" /><input value={form.database_url || ''} onChange={(e) => setForm({ ...form, database_url: e.target.value })} placeholder="DATABASE_URL" /><button className="primary" onClick={() => api('PUT', '/settings', form).then(() => { reload(); notify('设置已保存'); })}>保存</button></div></Panel>;
}
