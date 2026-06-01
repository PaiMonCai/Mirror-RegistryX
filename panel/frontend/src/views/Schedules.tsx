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

export function Schedules({ schedules, credentials, api, reload, notify }: any) {
  const emptyForm = { id: '', name: '', source: '', target: '', cron: '0 18 * * *', enabled: false, allow_latest: false, source_credential_id: '', target_credential_id: '' };
  const [form, setForm] = useState(emptyForm);
  const editing = Boolean(form.id);
  async function save() {
    await api('POST', '/schedules', { ...form, id: form.id || undefined });
    setForm(emptyForm);
    await reload();
    notify('计划推送已保存');
  }
  async function run(id: string) {
    await api('POST', `/schedules/${id}/run`, {});
    await reload();
    notify('计划推送已入队');
  }
  async function remove(id: string) {
    await api('DELETE', `/schedules/${id}`);
    await reload();
    notify('计划推送已删除');
  }
  async function toggle(item: AnyRecord) {
    await api('POST', '/schedules', { ...item, enabled: !item.enabled });
    await reload();
    notify(item.enabled ? '计划推送已停用' : '计划推送已启用');
  }
  function edit(item: AnyRecord) {
    setForm({
      id: item.id || '',
      name: item.name || '',
      source: item.source || '',
      target: item.target || '',
      cron: item.cron || '0 18 * * *',
      enabled: Boolean(item.enabled),
      allow_latest: Boolean(item.allow_latest),
      source_credential_id: item.source_credential_id || '',
      target_credential_id: item.target_credential_id || '',
    });
  }
  return (
    <div className="stack">
      <Panel title={editing ? `编辑计划 ${form.id}` : '新增计划'}>
        <div className="form-grid">
          <input placeholder="id" value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} disabled={editing} />
          <input placeholder="名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="源镜像" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
          <input placeholder="目标镜像" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} />
          <input placeholder="UTC cron，例如 0 18 * * *、*/30 * * * *" value={form.cron} onChange={(e) => setForm({ ...form, cron: e.target.value })} />
          <select value={form.source_credential_id} onChange={(e) => setForm({ ...form, source_credential_id: e.target.value })}><option value="">源凭据自动</option>{credentials.map((c: AnyRecord) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <select value={form.target_credential_id} onChange={(e) => setForm({ ...form, target_credential_id: e.target.value })}><option value="">目标凭据自动</option>{credentials.map((c: AnyRecord) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <label className="checkline"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />启用</label>
          <label className="checkline"><input type="checkbox" checked={form.allow_latest} onChange={(e) => setForm({ ...form, allow_latest: e.target.checked })} />允许 latest</label>
          <div className="row-actions">
            <button className="primary" onClick={save}>保存计划</button>
            {editing && <button onClick={() => setForm(emptyForm)}>取消编辑</button>}
          </div>
        </div>
      </Panel>
      <Panel title="计划列表">
        <table><thead><tr><th>ID</th><th>源</th><th>目标</th><th>Cron</th><th>启用</th><th>上次</th><th>下次</th><th>最近错误</th><th>操作</th></tr></thead><tbody>{schedules.map((item: AnyRecord) => <tr key={item.id}><td>{item.id}</td><td>{item.source}</td><td>{item.target}</td><td><span className="mono">{item.cron}</span><br /><small>{item.cron_timezone || 'UTC'}</small></td><td><Badge value={item.enabled ? 'enabled' : 'disabled'} /></td><td>{item.last_run_at || '-'}</td><td>{item.next_run_at || '-'}</td><td>{item.last_error || '-'}</td><td className="row-actions"><button onClick={() => edit(item)}>编辑</button><button onClick={() => toggle(item)}>{item.enabled ? '停用' : '启用'}</button><button onClick={() => run(item.id)}>运行</button><ConfirmButton confirmText="确认删除" onConfirm={() => remove(item.id)}><Trash2 size={14} /></ConfirmButton></td></tr>)}</tbody></table>
      </Panel>
    </div>
  );
}
