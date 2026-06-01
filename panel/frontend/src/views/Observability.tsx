import { RefreshCw } from 'lucide-react';
import { Badge, Metric, Panel } from '../components/common';
import type { AnyRecord } from '../types';
import { formatMB, formatRate, formatSeconds } from '../utils';

export function Observability({ data, reload }: { data: AnyRecord; reload: () => void }) {
  const window24 = data.windows?.['24h'] || {};
  const window7d = data.windows?.['7d'] || {};
  const alerts = data.alerts || [];
  const failures = data.failure_breakdown || [];
  const topFailedMirrors = data.top_failed_mirrors || [];
  const trend = data.trend || [];
  const queue = data.queue || {};
  const workers = data.workers || {};
  const notifications = data.notifications || {};
  const runtime = data.runtime || {};
  const cards = [
    ['健康', <Badge value={data.health || 'ok'} />],
    ['队列积压', queue.active ?? 0],
    ['Worker 最大心跳年龄', formatSeconds(workers.max_heartbeat_age_seconds ?? runtime.heartbeat_age_seconds)],
    ['24h 成功率', formatRate(window24.success_rate)],
    ['连续失败', data.metrics?.sync_consecutive_failures ?? 0],
    ['平均同步耗时', formatSeconds(runtime.avg_sync_duration_seconds_24h)],
    ['告警', alerts.length],
    ['Webhook', <Badge value={notifications.webhook_latest_status || (notifications.webhook_configured ? 'configured' : 'not_configured')} />],
  ];
  return (
    <div className="stack">
      <div className="metric-grid">{cards.map(([label, value]) => <Metric key={label} label={label as string} value={value} />)}</div>
      <Panel title="告警状态" action={<button onClick={reload}><RefreshCw size={16} />刷新</button>}>
        {alerts.length === 0 ? <p className="warn">当前无活动告警。</p> : (
          <table><thead><tr><th>级别</th><th>告警</th><th>说明</th><th>建议</th><th>指纹</th></tr></thead>
            <tbody>{alerts.map((item: AnyRecord) => <tr key={item.fingerprint}><td><Badge value={item.severity} /></td><td>{item.title}</td><td>{item.message}</td><td>{item.suggestion}</td><td className="mono">{item.fingerprint}</td></tr>)}</tbody>
          </table>
        )}
      </Panel>
      <Panel title="核心健康指标">
        <dl className="kv">
          <dt>24h 任务</dt><dd>{window24.total_runs ?? 0} 次，成功 {window24.completed_runs ?? 0}，失败 {window24.failed_runs ?? 0}，失败项 {window24.failed_items ?? 0}</dd>
          <dt>7d 成功率</dt><dd>{formatRate(window7d.success_rate)}（失败项 {window7d.failed_items ?? 0}）</dd>
          <dt>同步耗时</dt><dd>平均 {formatSeconds(runtime.avg_sync_duration_seconds_24h)}，最大 {formatSeconds(runtime.max_sync_duration_seconds_24h)}</dd>
          <dt>队列状态</dt><dd>active {queue.active ?? 0} / queued {queue.queued ?? 0} / running {queue.running ?? 0} / paused {queue.paused ?? 0}</dd>
          <dt>最老积压</dt><dd>{queue.oldest_active ? `#${queue.oldest_active.id} ${queue.oldest_active.status}，等待 ${formatSeconds(queue.oldest_active_age_seconds)}` : '-'}</dd>
          <dt>Worker</dt><dd>总计 {workers.total ?? 0}，在线 {workers.online ?? 0}，过期 {workers.stale ?? 0}</dd>
          <dt>Sync 心跳</dt><dd>{runtime.last_heartbeat || '-'}（{formatSeconds(runtime.heartbeat_age_seconds)}）</dd>
          <dt>磁盘余量</dt><dd>{formatMB(data.storage?.disk_free_bytes)}</dd>
        </dl>
      </Panel>
      <Panel title="失败聚合">
        {failures.length === 0 ? <p className="warn">最近 7 天无失败聚合。</p> : (
          <table><thead><tr><th>分类</th><th>次数</th><th>源 Registry</th><th>目标 Registry</th><th>镜像组</th><th>建议</th></tr></thead>
            <tbody>{failures.map((item: AnyRecord) => <tr key={`${item.category}-${item.source_registry}-${item.target_registry}-${item.group}`}><td><Badge value={item.category} /></td><td>{item.count}</td><td>{item.source_registry}</td><td>{item.target_registry}</td><td>{item.project}/{item.environment}/{item.group}</td><td>{item.suggestion || item.reason}</td></tr>)}</tbody>
          </table>
        )}
      </Panel>
      <Panel title="失败镜像 Top N">
        {topFailedMirrors.length === 0 ? <p className="warn">最近 7 天无失败镜像。</p> : (
          <table><thead><tr><th>源镜像</th><th>目标镜像</th><th>失败次数</th><th>最近失败</th></tr></thead>
            <tbody>{topFailedMirrors.map((item: AnyRecord) => <tr key={`${item.source}-${item.target}`}><td className="breakable mono">{item.source}</td><td className="breakable mono">{item.target || '-'}</td><td>{item.failed_count}</td><td>{item.latest_failed_at || '-'}</td></tr>)}</tbody>
          </table>
        )}
      </Panel>
      <Panel title="Worker 心跳">
        {(workers.workers || []).length === 0 ? <p className="warn">暂无 Worker 心跳记录。</p> : (
          <table><thead><tr><th>Worker</th><th>环境</th><th>状态</th><th>心跳年龄</th><th>版本</th><th>消息</th></tr></thead>
            <tbody>{workers.workers.map((item: AnyRecord) => <tr key={item.worker_id}><td className="mono">{item.name || item.worker_id}</td><td>{item.environment}</td><td><Badge value={item.status} /></td><td>{formatSeconds(item.heartbeat_age_seconds)}</td><td>{item.version || '-'}</td><td>{item.message || '-'}</td></tr>)}</tbody>
          </table>
        )}
      </Panel>
      <Panel title="同步趋势">
        <table><thead><tr><th>时间桶</th><th>任务数</th><th>成功</th><th>失败</th><th>失败项</th></tr></thead>
          <tbody>{trend.map((item: AnyRecord) => <tr key={item.bucket_start}><td>{item.bucket_start}</td><td>{item.total_runs}</td><td>{item.completed_runs}</td><td>{item.failed_runs}</td><td>{item.failed_items}</td></tr>)}</tbody>
        </table>
      </Panel>
      <Panel title="通知状态">
        <dl className="kv">
          <dt>Webhook</dt><dd>{notifications.webhook_configured ? '已配置' : '未配置'} / {notifications.webhook_latest_status || '-'}</dd>
          <dt>去重窗口</dt><dd>{notifications.dedupe_seconds ?? 1800} 秒</dd>
          <dt>上次发送</dt><dd>{notifications.last_sent_at || '-'}</dd>
          <dt>发送事件</dt><dd>{notifications.last_sent_event || '-'}</dd>
          <dt>上次抑制</dt><dd>{notifications.last_suppressed_at || '-'}</dd>
          <dt>抑制事件</dt><dd>{notifications.last_suppressed_event || '-'}</dd>
          <dt>上次错误</dt><dd>{notifications.last_error_at ? `${notifications.last_error_at} · ${notifications.last_error || '-'}` : '-'}</dd>
        </dl>
      </Panel>
    </div>
  );
}
