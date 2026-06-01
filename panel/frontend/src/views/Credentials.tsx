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

export function Credentials({ credentials, api, reload, notify }: any) {
  const [form, setForm] = useState({ id: '', name: '', registry_host: '', username: '', secret: '', scope: 'both' });
  async function save() {
    await api('POST', '/credentials', { ...form, id: form.id || undefined });
    setForm({ id: '', name: '', registry_host: '', username: '', secret: '', scope: 'both' });
    await reload();
    notify('凭据已保存');
  }
  return (
    <div className="stack">
      <Panel title="新增凭据">
        <div className="form-grid credentials-form">
          <input placeholder="凭据 ID（可选）" value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} />
          <input placeholder="显示名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="registry host，例如 ghcr.io" value={form.registry_host} onChange={(e) => setForm({ ...form, registry_host: e.target.value })} />
          <input placeholder="用户名" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input type="password" placeholder="token/password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
          <select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}><option value="both">源和目标</option><option value="source">仅源</option><option value="target">仅目标</option></select>
          <button className="primary" onClick={save}><KeyRound size={16} />保存凭据</button>
        </div>
      </Panel>
      <Panel title="已保存凭据">
        <table><thead><tr><th>ID</th><th>名称</th><th>Host</th><th>用户名</th><th>Scope</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{credentials.map((c: AnyRecord) => <tr key={c.id}><td>{c.id}</td><td>{c.name}</td><td>{c.registry_host}</td><td>{c.username}</td><td>{c.scope}</td><td><Badge value={c.configured ? 'configured' : 'empty'} /></td><td className="row-actions"><button onClick={() => api('POST', `/credentials/${c.id}/test`, {}).then((r: AnyRecord) => notify(`测试结果: ${r.status}`))}>测试</button><ConfirmButton confirmText="确认删除" onConfirm={() => api('DELETE', `/credentials/${c.id}`).then(reload)}><Trash2 size={14} /></ConfirmButton></td></tr>)}</tbody>
        </table>
      </Panel>
    </div>
  );
}
