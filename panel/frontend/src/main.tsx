import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { LogOut, Play, PlusCircle, Search } from 'lucide-react';
import { ApiError, createApiClient, formatApiError } from './api';
import { LoginScreen } from './components/LoginScreen';
import { navGroups, viewMeta } from './navigation';
import type { AnyRecord, Credential, Mirror, SyncQueueTask, SyncRun, View } from './types';
import { cx } from './utils';
import {
  Credentials,
  Dashboard,
  Logs,
  Mirrors,
  Runs,
  SettingsView,
  Storage,
} from './views';
import './styles.css';

function App() {
  const [view, setView] = useState<View>('dashboard');
  const [theme, setTheme] = useState(localStorage.getItem('mirrorRegistryTheme') || 'light');
  const [auth, setAuth] = useState<AnyRecord>({ loading: true, authenticated: false });
  const [status, setStatus] = useState<AnyRecord>({});
  const [mirrors, setMirrors] = useState<Mirror[]>([]);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [syncQueue, setSyncQueue] = useState<SyncQueueTask[]>([]);
  const [selectedRun, setSelectedRun] = useState<AnyRecord | null>(null);
  const [storage, setStorage] = useState<AnyRecord>({});
  const [logs, setLogs] = useState('');
  const [events, setEvents] = useState<AnyRecord[]>([]);
  const [settings, setSettings] = useState<AnyRecord>({});
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [toast, setToast] = useState('');
  const [search, setSearch] = useState('');
  const api = useMemo(() => createApiClient(), []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('mirrorRegistryTheme', theme);
  }, [theme]);

  function notify(message: string) {
    setToast(message);
    window.setTimeout(() => setToast(''), 2600);
  }

  async function loadAuth() {
    try {
      setAuth({ ...(await api('GET', '/auth/me')), loading: false });
    } catch (error: any) {
      if (error instanceof ApiError && error.status === 401) {
        setAuth({ loading: false, authenticated: false });
        return;
      }
      setAuth({ loading: false, authenticated: false, error: formatApiError(error) });
    }
  }

  async function login(username: string, password: string) {
    await api('POST', '/auth/login', { username, password });
    await loadAuth();
    await loadStatus();
  }

  async function logout() {
    await api('POST', '/auth/logout', {});
    setAuth({ loading: false, authenticated: false });
  }

  async function loadStatus() {
    setStatus(await api('GET', '/status'));
  }

  async function loadMirrors() {
    setMirrors(await api('GET', '/mirrors'));
  }

  async function loadRuns() {
    const [runRows, queueRows] = await Promise.all([
      api('GET', '/sync-runs?limit=30'),
      api('GET', '/sync-queue?limit=50'),
    ]);
    setRuns(runRows);
    setSyncQueue(queueRows);
  }

  async function loadStorage() {
    setStorage(await api('GET', '/storage'));
  }

  async function loadLogs() {
    setLogs((await api('GET', '/logs?lines=150')).text || '');
    setEvents(await api('GET', '/events?limit=100'));
  }

  async function loadSettings() {
    setSettings(await api('GET', '/settings'));
  }

  async function loadCredentials() {
    setCredentials(await api('GET', '/credentials'));
  }

  useEffect(() => {
    loadAuth();
  }, []);

  useEffect(() => {
    if (auth.authenticated) loadStatus().catch((error) => notify(formatApiError(error)));
  }, [auth.authenticated]);

  useEffect(() => {
    if (!auth.authenticated) return;
    const load = async () => {
      if (view === 'dashboard') await loadStatus();
      if (view === 'runs') await loadRuns();
      if (view === 'mirrors') {
        await loadMirrors();
        await loadCredentials();
      }
      if (view === 'credentials') await loadCredentials();
      if (view === 'storage') await loadStorage();
      if (view === 'logs') await loadLogs();
      if (view === 'settings') await loadSettings();
    };
    load().catch((error) => notify(formatApiError(error)));
  }, [view, auth.authenticated]);

  const filteredMirrors = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return mirrors;
    return mirrors.filter((item) => JSON.stringify(item).toLowerCase().includes(term));
  }, [mirrors, search]);

  async function action(label: string, fn: () => Promise<void>) {
    try {
      await fn();
      notify(label);
    } catch (error: any) {
      notify(formatApiError(error));
    }
  }

  if (auth.loading) {
    return <div className="auth-page"><div className="login-card"><div className="brand-mark">MR</div><h1>Mirror Registry</h1><p>正在检查登录状态...</p></div></div>;
  }

  if (!auth.authenticated) {
    return <LoginScreen auth={auth} login={login} theme={theme} setTheme={setTheme} />;
  }

  return (
    <>
      <div className="bg-canvas">
        <div className="orb orb1"></div>
        <div className="orb orb2"></div>
        <div className="orb orb3"></div>
      </div>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-mark">🐳</div>
            <div>
              <strong>MirrorSync</strong>
              <span>v0.1.0-dev</span>
            </div>
          </div>
          <nav aria-label="主导航">
            {navGroups.map((group) => (
              <div className="nav-group" key={group.label}>
                <span className="nav-group-label">{group.label}</span>
                <div className="nav-group-items">
                  {group.views.map((name) => (
                    <button key={name} className={cx('nav-button', view === name && 'active')} onClick={() => setView(name)}>
                      {viewMeta[name].icon}
                      <span>{viewMeta[name].title}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </nav>
          <div className="session-card">
            <div className="session-avatar">{String(auth.user?.username || 'admin').slice(0, 2).toUpperCase()}</div>
            <div className="session-meta">
              <strong>{auth.user?.username || 'admin'}</strong>
              <span>{auth.user?.role || '超级管理员'}</span>
            </div>
            <span aria-hidden="true">↔</span>
          </div>
        </aside>

        <div className="main">
          <header className="topbar">
            <div>
              <h1>{viewMeta[view].title}</h1>
              <p>{viewMeta[view].subtitle}</p>
            </div>
            <div className="top-actions">
              <label className="search" aria-label="搜索镜像、仓库">
                <Search size={14} />
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索镜像、仓库…" />
              </label>
              <button onClick={() => setView('mirrors')}><PlusCircle size={16} />添加镜像</button>
              <button className="primary" onClick={() => action('同步已入队', async () => { await api('POST', '/sync'); await loadStatus(); if (view === 'runs') await loadRuns(); })}>
                <Play size={16} />立即同步
              </button>
              <button className="ghost" onClick={() => action('已退出登录', logout)} title="退出登录">
                <LogOut size={16} />
              </button>
            </div>
          </header>

          <main>
        {view === 'dashboard' && <Dashboard status={status} reload={() => action('已刷新', loadStatus)} setView={setView} />}
        {view === 'runs' && <Runs runs={runs} syncQueue={syncQueue} selectedRun={selectedRun} setSelectedRun={setSelectedRun} api={api} reload={loadRuns} notify={notify} />}
        {view === 'mirrors' && <Mirrors mirrors={filteredMirrors} credentials={credentials} search={search} setSearch={setSearch} api={api} reload={async () => { await loadMirrors(); await loadCredentials(); }} notify={notify} />}
        {view === 'credentials' && <Credentials credentials={credentials} api={api} reload={loadCredentials} notify={notify} />}
        {view === 'storage' && <Storage storage={storage} api={api} reload={loadStorage} notify={notify} />}
        {view === 'logs' && <Logs logs={logs} events={events} reload={loadLogs} />}
          {view === 'settings' && <SettingsView settings={settings} api={api} reload={loadSettings} notify={notify} />}
          </main>
        </div>

        {toast && <div className="toast">{toast}</div>}
      </div>
    </>
  );
}


createRoot(document.getElementById('root')!).render(<App />);
