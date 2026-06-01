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

export function AccessControl({ access, api, reload, notify }: any) {
  const [userForm, setUserForm] = useState({ username: '', password: '', role: 'viewer' });
  const [tokenForm, setTokenForm] = useState({ name: '', role: 'operator', scopes: 'sync' });
  const [issuedToken, setIssuedToken] = useState('');
  async function saveUser() {
    await api('POST', '/access/users', { ...userForm, password: userForm.password || undefined });
    setUserForm({ username: '', password: '', role: 'viewer' });
    await reload();
    notify('用户已保存');
  }
  async function createToken() {
    const result = await api('POST', '/access/tokens', { ...tokenForm, scopes: tokenForm.scopes.split(',').map((item: string) => item.trim()).filter(Boolean) });
    setIssuedToken(result.token);
    setTokenForm({ name: '', role: 'operator', scopes: 'sync' });
    await reload();
    notify('API Token 已创建');
  }
  return (
    <div className="stack">
      <Panel title="本地用户">
        <div className="form-grid">
          <input placeholder="username" value={userForm.username} onChange={(e) => setUserForm({ ...userForm, username: e.target.value })} />
          <input type="password" placeholder="password（新用户必填）" value={userForm.password} onChange={(e) => setUserForm({ ...userForm, password: e.target.value })} />
          <select value={userForm.role} onChange={(e) => setUserForm({ ...userForm, role: e.target.value })}><option value="viewer">viewer</option><option value="operator">operator</option><option value="admin">admin</option></select>
          <button className="primary" onClick={saveUser}>保存用户</button>
        </div>
        <table><thead><tr><th>用户</th><th>角色</th><th>创建</th><th>更新</th><th>操作</th></tr></thead>
          <tbody>{(access.users || []).map((user: AnyRecord) => <tr key={user.username}><td>{user.username}</td><td><Badge value={user.role} /></td><td>{user.created_at}</td><td>{user.updated_at}</td><td><ConfirmButton confirmText="确认删除" onConfirm={() => api('DELETE', `/access/users/${user.username}`).then(reload)}><Trash2 size={14} /></ConfirmButton></td></tr>)}</tbody>
        </table>
      </Panel>
      <Panel title="API Token">
        <div className="form-grid">
          <input placeholder="名称" value={tokenForm.name} onChange={(e) => setTokenForm({ ...tokenForm, name: e.target.value })} />
          <select value={tokenForm.role} onChange={(e) => setTokenForm({ ...tokenForm, role: e.target.value })}><option value="operator">operator</option><option value="viewer">viewer</option><option value="admin">admin</option></select>
          <input placeholder="scopes, comma separated" value={tokenForm.scopes} onChange={(e) => setTokenForm({ ...tokenForm, scopes: e.target.value })} />
          <button className="primary" onClick={createToken}><KeyRound size={16} />创建 Token</button>
        </div>
        {issuedToken && <pre>{issuedToken}</pre>}
        <table><thead><tr><th>ID</th><th>名称</th><th>角色</th><th>Scopes</th><th>状态</th><th>最近使用</th><th>操作</th></tr></thead>
          <tbody>{(access.tokens || []).map((token: AnyRecord) => <tr key={token.id}><td>{token.id}</td><td>{token.name}</td><td><Badge value={token.role} /></td><td>{(token.scopes || []).join(', ')}</td><td><Badge value={token.revoked ? 'revoked' : 'active'} /></td><td>{token.last_used_at || '-'}</td><td>{!token.revoked && <ConfirmButton confirmText="确认撤销" className="" onConfirm={() => api('POST', `/access/tokens/${token.id}/revoke`, {}).then(reload)}>撤销</ConfirmButton>}</td></tr>)}</tbody>
        </table>
      </Panel>
    </div>
  );
}
