import { Badge, Metric, Panel } from '../components/common';
import type { AnyRecord } from '../types';
import { formatMB, formatRate, formatSeconds } from '../utils';

export function Observability({ observability, reload }: any) {
  const summary = observability.summary || {};
  const metrics = observability.metrics || {};
  const window24h = summary.windows?.['24h'] || {};
  const window7d = summary.windows?.['7d'] || {};
  const activeAlerts = summary.alerts || metrics.alerts || [];
  const trend = summary.trend || [];
  const failures = summary.failure_breakdown || [];

  return (
    <div className="stack">
      <div className="metric-grid">
        <Metric label="成功率" value={formatRate(window24h.success_rate ?? summary.metrics?.sync_success_rate_24h)} />
        <Metric label="24h 失败" value={window24h.failed_runs ?? 0} />
        <Metric label="7d 失败项" value={window7d.failed_items ?? 0} />
        <Metric label="告警状态" value={<Badge value={summary.health || metrics.health || 'ok'} />} />
        <Metric label="队列活跃" value={summary.queue?.active ?? summary.metrics?.sync_queue_active ?? 0} />
        <Metric label="平均耗时" value={formatSeconds(summary.runtime?.avg_sync_duration_seconds_24h)} />
      </div>

      <Panel title="可观测" action={<button onClick={reload}>刷新</button>}>
        <dl className="kv">
          <dt>生成时间</dt><dd>{summary.generated_at || metrics.generated_at || '-'}</dd>
          <dt>版本</dt><dd>{summary.version?.image_tag || metrics.version?.image_tag || '-'}</dd>
          <dt>磁盘</dt><dd>{summary.storage?.disk_low ? '低空间' : '正常'} / {formatMB(summary.storage?.disk_free_bytes)}</dd>
          <dt>Webhook</dt><dd>{summary.notifications?.webhook_latest_status || '-'}</dd>
        </dl>
      </Panel>

      <Panel title="失败聚合">
        <table>
          <thead><tr><th>类别</th><th className="num">次数</th><th>样例</th></tr></thead>
          <tbody>{failures.map((item: AnyRecord) => (
            <tr key={item.category}>
              <td><Badge value={item.category} /></td>
              <td className="num">{item.count}</td>
              <td className="breakable">{item.sample || item.message || '-'}</td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>

      <Panel title="告警状态">
        <div className="check-grid">
          {activeAlerts.length === 0 && <div className="check"><strong>无活跃告警</strong><span>当前没有需要处理的监控告警。</span></div>}
          {activeAlerts.map((alert: AnyRecord) => (
            <div className="check" key={alert.id || alert.fingerprint}>
              <div><Badge value={alert.level || alert.severity || 'warn'} /> <strong>{alert.title || alert.id}</strong></div>
              <span>{alert.message || alert.suggestion || '-'}</span>
              <small className="mono">{alert.fingerprint || alert.id}</small>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="同步趋势">
        <table>
          <thead><tr><th>时间窗口</th><th className="num">总数</th><th className="num">成功</th><th className="num">失败</th></tr></thead>
          <tbody>{trend.map((item: AnyRecord, index: number) => (
            <tr key={item.bucket || item.date || index}>
              <td>{item.bucket || item.date || '-'}</td>
              <td className="num">{item.total ?? item.total_runs ?? '-'}</td>
              <td className="num">{item.completed ?? item.completed_runs ?? '-'}</td>
              <td className="num">{item.failed ?? item.failed_runs ?? '-'}</td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>
    </div>
  );
}
