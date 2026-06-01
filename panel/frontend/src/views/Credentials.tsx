import { useState } from 'react';
import { Edit3, KeyRound, Trash2, X } from 'lucide-react';
import { Badge, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord } from '../types';

const emptyForm = { id: '', name: '', registry_host: '', username: '', secret: '', scope: 'both' };

export function Credentials({ credentials, api, reload, notify }: any) {
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState('');
  const [testUrls, setTestUrls] = useState<Record<string, string>>({});

  const isEditing = Boolean(editingId);

  function editCredential(credential: AnyRecord) {
    setEditingId(credential.id);
    setForm({
      id: credential.id,
      name: credential.name || '',
      registry_host: credential.registry_host || '',
      username: credential.username || '',
      secret: '',
      scope: credential.scope || 'both',
    });
  }

  function cancelEdit() {
    setEditingId('');
    setForm(emptyForm);
  }

  async function testCredential(credential: AnyRecord) {
    const registryUrl = (testUrls[credential.id] || '').trim();
    const result = await api('POST', `/credentials/${encodeURIComponent(credential.id)}/test`, registryUrl ? { registry_url: registryUrl } : {});
    const target = result.registry_url || result.check_url || credential.registry_host;
    notify(`${credential.name || credential.id}: ${result.status}，${result.message || ''} ${target ? `(${target})` : ''}`.trim());
  }

  async function save() {
    if (isEditing) {
      const payload: AnyRecord = {
        name: form.name,
        registry_host: form.registry_host,
        username: form.username,
        scope: form.scope,
      };
      if (form.secret) payload.secret = form.secret;
      await api('PUT', `/credentials/${encodeURIComponent(editingId)}`, payload);
      notify(form.secret ? '凭据已更新，密钥已替换' : '凭据已更新，密钥保持不变');
    } else {
      await api('POST', '/credentials', { ...form, id: form.id || undefined });
      notify('凭据已保存');
    }
    cancelEdit();
    await reload();
  }

  return (
    <div className="stack">
      <Panel title={isEditing ? `编辑凭据：${editingId}` : '新增凭据'} action={isEditing ? <button onClick={cancelEdit}><X size={14} />取消编辑</button> : null}>
        <p className="sect-desc compact">
          凭据用于访问私有源仓库或目标 Registry。编辑已有凭据时，token/password 留空表示沿用原密钥；填写后会替换旧密钥。
        </p>
        <div className="form-grid credentials-form roomy-form">
          <input placeholder="凭据 ID（创建后不可改）" value={form.id} disabled={isEditing} onChange={(e) => setForm({ ...form, id: e.target.value })} />
          <input placeholder="显示名称" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="registry host，例如 ghcr.io" value={form.registry_host} onChange={(e) => setForm({ ...form, registry_host: e.target.value })} />
          <input placeholder="用户名" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input type="password" placeholder={isEditing ? '新 token/password（留空不变）' : 'token/password'} value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
          <select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}><option value="both">源和目标</option><option value="source">仅源</option><option value="target">仅目标</option></select>
          <button className="primary" onClick={save}><KeyRound size={16} />{isEditing ? '保存修改' : '保存凭据'}</button>
        </div>
      </Panel>
      <Panel title="已保存凭据">
        <div className="table-scroll"><table><thead><tr><th>ID</th><th>名称</th><th>Host</th><th>用户名</th><th>Scope</th><th>状态</th><th>测试 URL（可选）</th><th>操作</th></tr></thead>
          <tbody>{credentials.map((c: AnyRecord) => <tr key={c.id}><td>{c.id}</td><td>{c.name}</td><td>{c.registry_host}</td><td>{c.username}</td><td>{c.scope}</td><td><Badge value={c.configured ? 'configured' : 'empty'} /></td><td><input className="table-input" placeholder="留空自动判断，如 http://localhost:5000" value={testUrls[c.id] || ''} onChange={(event) => setTestUrls({ ...testUrls, [c.id]: event.target.value })} /></td><td className="row-actions"><button onClick={() => editCredential(c)}><Edit3 size={14} />编辑</button><button onClick={() => testCredential(c)}>测试</button><ConfirmButton confirmText="确认删除" onConfirm={() => api('DELETE', `/credentials/${encodeURIComponent(c.id)}`).then(reload)}><Trash2 size={14} /></ConfirmButton></td></tr>)}</tbody>
        </table></div>
      </Panel>
    </div>
  );
}
