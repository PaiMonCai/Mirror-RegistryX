import { useState, type FormEvent } from 'react';
import { LockKeyhole, Moon, Sun } from 'lucide-react';
import type { AnyRecord } from '../types';

type LoginScreenProps = {
  auth: AnyRecord;
  login: (username: string, password: string) => Promise<void>;
  theme: string;
  setTheme: (theme: string) => void;
};

export function LoginScreen({ auth, login, theme, setTheme }: LoginScreenProps) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(auth.error || '');
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await login(username, password);
    } catch (err: any) {
      setError(err.message || '登录失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="auth-page">
      <section className="login-card">
        <div className="login-head">
          <div className="brand-mark">MR</div>
          <button className="ghost" type="button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title="切换主题">
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
        <div>
          <h1>Mirror Registry</h1>
          <p>登录后管理镜像同步、仓库凭据、治理策略和存储统计。</p>
        </div>
        <form className="login-form" onSubmit={submit}>
          <label>
            <span>管理员账号</span>
            <input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
          </label>
          <label>
            <span>密码</span>
            <input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          {error && <p className="form-error">{error}</p>}
          {auth.admin_initialized === false && <p className="warn">管理员未初始化，请先设置 ADMIN_USERNAME / ADMIN_PASSWORD 并重启 panel。</p>}
          <button className="primary login-submit" disabled={submitting} type="submit">
            <LockKeyhole size={16} />{submitting ? '登录中...' : '登录'}
          </button>
        </form>
      </section>
    </div>
  );
}
