import { AlertTriangle, CheckCircle2, Database, HardDrive, RefreshCw, Settings2 } from 'lucide-react';
import { Badge, Metric, Panel } from '../components/common';
import type { AnyRecord } from '../types';
import { cx, formatMB } from '../utils';

export function Dashboard({
  status,
  opsAgents = [],
  opsTasks = [],
  reload,
  setView,
}: {
  status: AnyRecord;
  opsAgents?: AnyRecord[];
  opsTasks?: AnyRecord[];
  reload: () => void;
  setView: (view: any) => void;
}) {
  const latestRun = status.latest_run;
  const onlineOpsAgents = opsAgents.filter((agent) => agent.status === 'online');
  const latestOpsTask = opsTasks[0];
  const health = status.disk_low ? 'warn' : latestRun?.status === 'failed' ? 'error' : 'ok';
  const healthCopy: Record<string, { title: string; body: string }> = {
    ok: { title: '本地镜像服务运行正常', body: '面板、同步队列和本地 Registry 已就绪。需要新增镜像时，直接从镜像配置开始。' },
    warn: { title: '存储空间需要关注', body: '磁盘剩余空间偏低。建议先查看存储页，再决定是否清理旧 tag。' },
    error: { title: '最近一次同步失败', body: '同步任务出现失败项。先打开任务页看错误，再按需重试或重新保存凭据。' },
  };
  const healthInfo = healthCopy[health] || healthCopy.ok;
  const nextActions = [
    { label: '添加镜像', view: 'mirrors', tone: 'default' },
    { label: '保存凭据', view: 'credentials', tone: 'default' },
    { label: '查看任务', view: 'runs', tone: latestRun?.status === 'failed' ? 'danger' : 'default' },
    { label: '运维操作', view: 'operations', tone: 'default' },
    { label: '查看日志', view: 'logs', tone: 'default' },
  ].filter(Boolean) as Array<{ label: string; view: any; tone: string }>;
  const cards = [
    ['健康', <Badge value={health} />],
    ['镜像', status.total ?? 0],
    ['已同步', status.synced ?? 0],
    ['待同步', status.pending ?? 0],
    ['同步状态', status.is_syncing ? '运行中' : '空闲'],
    ['运维代理', opsAgents.length ? `${onlineOpsAgents.length}/${opsAgents.length} 在线` : '未注册'],
    ['版本', status.image_tag || status.app_version || '-'],
  ];
  return (
    <section className="stack">
      <Panel title="运行概览" action={<button onClick={reload}><RefreshCw size={16} />刷新</button>}>
        <div className={cx('dashboard-hero', health)}>
          <div className="dashboard-hero-main">
            <span className="hero-icon">{health === 'ok' ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}</span>
            <div>
              <h2>{healthInfo.title}</h2>
              <p>{healthInfo.body}</p>
              <div className="chip-list compact">
                <span className="chip">{status.is_syncing ? '同步中' : '空闲'}</span>
                <span className="chip">间隔 {status.interval ?? '-'} 分钟</span>
                <span className="chip">并发 {status.sync_concurrency ?? '-'}</span>
              </div>
            </div>
          </div>
          <div className="dashboard-next-actions">
            <span>建议操作</span>
            <div className="row-actions">
              {nextActions.slice(0, 4).map((item) => <button key={item.label} className={cx(item.tone === 'danger' && 'danger')} onClick={() => setView(item.view)}>{item.label}</button>)}
            </div>
          </div>
        </div>
      </Panel>
      <div className="metric-grid">{cards.map(([label, value]) => <Metric key={label} label={label as string} value={value} />)}</div>
      <div className="dashboard-grid">
        <Panel title="同步概况">
          <dl className="kv">
            <dt>状态</dt><dd>{status.is_syncing ? '运行中' : '空闲'}</dd>
            <dt>最近任务</dt><dd>{latestRun ? `${latestRun.status} · 更新 ${latestRun.updated ?? 0} · 失败 ${latestRun.failed ?? 0}` : '-'}</dd>
            <dt>上次开始</dt><dd>{status.last_started_at || '-'}</dd>
            <dt>下次计划</dt><dd>{status.next_run_at || '-'}</dd>
          </dl>
        </Panel>
        <Panel title="本机配置">
          <dl className="kv">
            <dt><Database size={14} /> 数据库</dt><dd>{status.database_backend || '-'}</dd>
            <dt><Settings2 size={14} /> 重试</dt><dd>{status.sync_retry_count ?? '-'}</dd>
            <dt>Cookie</dt><dd>{status.session_cookie_secure ? 'HTTPS' : 'HTTP'}</dd>
            <dt>认证</dt><dd>{status.auth_required ? '已启用' : '未启用'}</dd>
          </dl>
        </Panel>
        <Panel title="运维代理">
          <dl className="kv">
            <dt>代理</dt><dd>{opsAgents.length ? `${onlineOpsAgents.length}/${opsAgents.length} 在线` : '未注册'}</dd>
            <dt>最近任务</dt><dd>{latestOpsTask ? `${latestOpsTask.action} · ${latestOpsTask.status}` : '-'}</dd>
            <dt>失败任务</dt><dd>{opsTasks.find((task) => ['failed', 'timed_out'].includes(task.status))?.id || '-'}</dd>
            <dt>下步入口</dt><dd><button onClick={() => setView('operations')}>打开运维面板</button></dd>
          </dl>
        </Panel>
        <Panel title="存储状态">
          <dl className="kv">
            <dt><HardDrive size={14} /> 磁盘状态</dt><dd>{status.disk_low ? '低空间' : '正常'}</dd>
            <dt>剩余空间</dt><dd>{formatMB(status.disk_free_bytes)}</dd>
            <dt>同步引擎</dt><dd>{status.sync_engine || 'skopeo'}</dd>
            <dt>下步入口</dt><dd><button onClick={() => setView('storage')}>打开存储管理</button></dd>
          </dl>
        </Panel>
      </div>
    </section>
  );
}
