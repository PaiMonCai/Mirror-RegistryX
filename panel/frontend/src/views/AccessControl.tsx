import { useState } from 'react';
import { KeyRound, Trash2 } from 'lucide-react';
import { Badge, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord } from '../types';

export function AccessControl({ access, api, reload, notify }: any) {
  const [userForm, setUserForm] = useState({ username: '', password: '', role: 'viewer' });
  const [ownPassword, setOwnPassword] = useState({ current_password: '', new_password: '', confirm_password: '' });
  const [resetForm, setResetForm] = useState({ username: '', new_password: '', confirm_password: '' });

  async function saveUser() {
    await api('POST', '/access/users', { ...userForm, password: userForm.password || undefined });
    setUserForm({ username: '', password: '', role: 'viewer' });
    await reload();
    notify('用户已保存');
  }

  async function changeOwnPassword() {
    if (ownPassword.new_password !== ownPassword.confirm_password) {
      notify('两次输入的新密码不一致');
      return;
    }
    await api('POST', '/auth/change-password', {
      current_password: ownPassword.current_password,
      new_password: ownPassword.new_password,
    });
    setOwnPassword({ current_password: '', new_password: '', confirm_password: '' });
    notify('密码已修改，请重新登录');
  }

  async function resetPassword() {
    if (!resetForm.username) {
      notify('请选择要重置的用户');
      return;
    }
    if (resetForm.new_password !== resetForm.confirm_password) {
      notify('两次输入的新密码不一致');
      return;
    }
    await api('POST', `/access/users/${encodeURIComponent(resetForm.username)}/reset-password`, {
      new_password: resetForm.new_password,
    });
    setResetForm({ username: '', new_password: '', confirm_password: '' });
    await reload();
    notify('用户密码已重置');
  }

  return (
    <div className="stack">
      <Panel title="修改当前账号密码">
        <div className="form-grid password-form-grid">
          <input type="password" autoComplete="current-password" placeholder="当前密码" value={ownPassword.current_password} onChange={(e) => setOwnPassword({ ...ownPassword, current_password: e.target.value })} />
          <input type="password" autoComplete="new-password" placeholder="新密码（至少 8 位）" value={ownPassword.new_password} onChange={(e) => setOwnPassword({ ...ownPassword, new_password: e.target.value })} />
          <input type="password" autoComplete="new-password" placeholder="确认新密码" value={ownPassword.confirm_password} onChange={(e) => setOwnPassword({ ...ownPassword, confirm_password: e.target.value })} />
          <button className="primary" onClick={changeOwnPassword}><KeyRound size={14} />修改密码</button>
        </div>
        <p className="sect-desc compact">修改成功后会清除当前账号的所有登录会话，需要重新登录。</p>
      </Panel>

      <Panel title="管理员重置用户密码">
        <div className="form-grid password-form-grid">
          <select value={resetForm.username} onChange={(e) => setResetForm({ ...resetForm, username: e.target.value })}>
            <option value="">选择用户</option>
            {(access.users || []).map((user: AnyRecord) => <option key={user.username} value={user.username}>{user.username}</option>)}
          </select>
          <input type="password" autoComplete="new-password" placeholder="新密码（至少 8 位）" value={resetForm.new_password} onChange={(e) => setResetForm({ ...resetForm, new_password: e.target.value })} />
          <input type="password" autoComplete="new-password" placeholder="确认新密码" value={resetForm.confirm_password} onChange={(e) => setResetForm({ ...resetForm, confirm_password: e.target.value })} />
          <button onClick={resetPassword}><KeyRound size={14} />重置密码</button>
        </div>
      </Panel>

      <Panel title="本地用户">
        <div className="form-grid">
          <input placeholder="username" value={userForm.username} onChange={(e) => setUserForm({ ...userForm, username: e.target.value })} />
          <input type="password" placeholder="password（新用户必填，至少 8 位）" value={userForm.password} onChange={(e) => setUserForm({ ...userForm, password: e.target.value })} />
          <select value={userForm.role} onChange={(e) => setUserForm({ ...userForm, role: e.target.value })}><option value="viewer">viewer</option><option value="operator">operator</option><option value="admin">admin</option></select>
          <button className="primary" onClick={saveUser}>保存用户</button>
        </div>
        <div className="table-scroll"><table><thead><tr><th>用户</th><th>角色</th><th>创建</th><th>更新</th><th>操作</th></tr></thead>
          <tbody>{(access.users || []).map((user: AnyRecord) => <tr key={user.username}><td>{user.username}</td><td><Badge value={user.role} /></td><td>{user.created_at}</td><td>{user.updated_at}</td><td><ConfirmButton confirmText="确认删除" onConfirm={() => api('DELETE', `/access/users/${encodeURIComponent(user.username)}`).then(reload)}><Trash2 size={14} /></ConfirmButton></td></tr>)}</tbody>
        </table></div>
      </Panel>
    </div>
  );
}
