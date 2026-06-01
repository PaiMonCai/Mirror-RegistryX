import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import { Badge, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord } from '../types';

export function AccessControl({ access, api, reload, notify }: any) {
  const [userForm, setUserForm] = useState({ username: '', password: '', role: 'viewer' });
  async function saveUser() {
    await api('POST', '/access/users', { ...userForm, password: userForm.password || undefined });
    setUserForm({ username: '', password: '', role: 'viewer' });
    await reload();
    notify('用户已保存');
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
    </div>
  );
}
