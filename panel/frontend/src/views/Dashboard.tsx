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
import { formatApiError } from '../api';
import { Badge, Metric, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord, View, Mirror, Credential } from '../types';
import { cx, diagnosticMessage, formatMB, formatRate, hostFromImage } from '../utils';

export function Dashboard({ status, ops, api, notify, reload, setView }: { status: AnyRecord; ops: AnyRecord; api: any; notify: (message: string) => void; reload: () => void; setView: (view: any) => void }) {
  const failures = ops.sync?.recent_failures || [];
  const health = ops.health || 'ok';
  const healthCopy: Record<string, { title: string; body: string }> = {
    ok: { title: '镜像流水线运行正常', body: '定时检测、拉取和推送链路未发现阻断问题，可以继续维护镜像源与发布策略。' },
    warn: { title: '流水线需要关注', body: '存在默认令牌、删除标记或待处理队列，建议在低峰期处理，避免影响后续定时推送。' },
    error: { title: '同步链路需要处理', body: '最近的 Docker 镜像拉取、标记或推送出现异常，优先查看失败任务和诊断结果。' },
  };
  const healthInfo = healthCopy[health] || healthCopy.ok;
  const latestRun = ops.sync?.latest_run;
  const reasonLabels: Record<string, string> = {
    disk_low: '磁盘空间不足',
    latest_run_failed: '最近同步失败',
    sync_active: '同步正在运行',
    pending_deletion_marks: '存在删除标记',
    default_panel_token: 'PANEL_TOKEN 仍为默认值',
  };
  const nextActions = [
    failures.length > 0 && { label: '查看失败任务', view: 'runs', tone: 'danger' },
    ops.storage?.deletion_marks > 0 && { label: '处理删除标记', view: 'storage', tone: 'warn' },
    status.using_default_token && { label: '加固访问控制', view: 'access', tone: 'warn' },
    { label: '运行诊断', view: 'diagnostics', tone: 'default' },
    { label: '维护镜像配置', view: 'mirrors', tone: 'default' },
  ].filter(Boolean) as Array<{ label: string; view: any; tone: string }>;
  async function exportBundle() {
    try {
      const bundle = await api('GET', '/ops/diagnostic-bundle');
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `mirror-registry-diagnostic-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
      notify('诊断包已导出');
    } catch (error) {
      notify(formatApiError(error));
    }
  }
  const cards = [
    ['健康', <Badge value={health} />],
    ['镜像', ops.config?.mirrors ?? status.total ?? 0],
    ['待同步', ops.config?.pending ?? status.pending ?? 0],
    ['失败', failures.length],
    ['删除标记', ops.storage?.deletion_marks ?? 0],
    ['版本', ops.version?.image_tag || status.image_tag || '-'],
  ];
  return (
    <section className="stack">
      <Panel title="运维摘要" action={<div className="row-actions"><button onClick={reload}><RefreshCw size={16} />刷新</button><button onClick={exportBundle}><FileKey2 size={16} />导出诊断包</button></div>}>
        <div className={cx('dashboard-hero', health)}>
          <div className="dashboard-hero-main">
            <span className="hero-icon">{health === 'ok' ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}</span>
            <div>
              <h2>{healthInfo.title}</h2>
              <p>{healthInfo.body}</p>
              <div className="chip-list compact">
                {(ops.reasons || []).length === 0 ? <span className="chip">无活跃告警</span> : (ops.reasons || []).map((reason: string) => <span className="chip" key={reason}>{reasonLabels[reason] || reason}</span>)}
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
            <dt>同步状态</dt><dd>{ops.sync?.running ? '运行中' : '空闲'}</dd>
            <dt>最近任务</dt><dd>{latestRun ? `${latestRun.status} · updated ${latestRun.updated} · failed ${latestRun.failed}` : '-'}</dd>
            <dt>待同步</dt><dd>{ops.config?.pending ?? status.pending ?? 0}</dd>
            <dt>最近失败</dt><dd>{failures.length}</dd>
          </dl>
        </Panel>
        <Panel title="平台与安全">
          <dl className="kv">
            <dt>数据库</dt><dd>{ops.config?.database_backend || status.database_backend || '-'}</dd>
            <dt>Registry</dt><dd>{ops.config?.registries ?? status.registries ?? 0}</dd>
            <dt>镜像组</dt><dd>{ops.config?.mirror_groups ?? status.mirror_groups ?? 0}</dd>
            <dt>认证状态</dt><dd>{ops.security?.auth_required ? '已启用' : '未启用'}{ops.security?.using_default_token ? ' · 默认令牌' : ''}</dd>
          </dl>
        </Panel>
        <Panel title="存储状态">
          <dl className="kv">
            <dt>磁盘状态</dt><dd>{ops.storage?.disk_low ? '低空间' : '正常'}</dd>
            <dt>剩余空间</dt><dd>{ops.storage?.disk_free_bytes || '-'}</dd>
            <dt>删除标记</dt><dd>{ops.storage?.deletion_marks ?? 0}</dd>
            <dt>下步入口</dt><dd><button onClick={() => setView('storage')}>打开存储管理</button></dd>
          </dl>
        </Panel>
      </div>
      {failures.length > 0 && <Panel title="最近失败">
        <table><thead><tr><th>镜像</th><th>阶段</th><th>原因</th><th>建议</th></tr></thead>
          <tbody>{failures.map((item: AnyRecord) => <tr key={item.id}><td>{item.source}</td><td>{item.step || '-'}</td><td>{item.explanation?.reason || item.error}</td><td>{item.explanation?.suggestion || '-'}</td></tr>)}</tbody>
        </table>
      </Panel>}
    </section>
  );
}
