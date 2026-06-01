import { Badge, Metric, Panel } from '../components/common';
import type { AnyRecord } from '../types';

export function Workers({ workers, workerGuide, reload }: any) {
  const rows = workers || [];
  const guide = workerGuide || {};

  return (
    <div className="stack">
      <div className="metric-grid">
        <Metric label="Worker 状态" value={rows.length} />
        <Metric label="在线" value={rows.filter((item: AnyRecord) => item.status === 'online').length} />
        <Metric label="接入令牌" value={guide.enabled ? <Badge value="enabled" /> : <Badge value="disabled" />} />
      </div>

      <Panel title="Worker 状态" action={<button onClick={reload}>刷新</button>}>
        <table>
          <thead><tr><th>ID</th><th>名称</th><th>环境</th><th>状态</th><th>标签</th><th>能力</th><th>最近心跳</th><th>最近任务</th></tr></thead>
          <tbody>{rows.map((item: AnyRecord) => (
            <tr key={item.worker_id}>
              <td className="mono breakable">{item.worker_id}</td>
              <td>{item.name || '-'}</td>
              <td>{item.environment || '-'}</td>
              <td><Badge value={item.status} /></td>
              <td>{(item.labels || []).join(', ') || '-'}</td>
              <td>{(item.capabilities || []).join(', ') || '-'}</td>
              <td>{item.last_heartbeat || '-'}</td>
              <td className="breakable">{item.latest_claim ? JSON.stringify(item.latest_claim) : '-'}</td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>

      <Panel title="Worker 接入">
        <dl className="kv">
          <dt>启用状态</dt><dd><Badge value={guide.enabled ? 'enabled' : 'disabled'} /></dd>
          <dt>Token 环境变量</dt><dd className="mono">{guide.token_env || 'WORKER_TOKEN'}</dd>
          <dt>请求头</dt><dd className="mono">{guide.header || 'X-Worker-Token'}</dd>
          <dt>心跳</dt><dd className="mono breakable">{guide.endpoints?.heartbeat || 'POST /api/workers/heartbeat'}</dd>
          <dt>领取任务</dt><dd className="mono breakable">{guide.endpoints?.claim || 'POST /api/workers/claim'}</dd>
          <dt>完成任务</dt><dd className="mono breakable">{guide.endpoints?.complete || 'POST /api/workers/complete'}</dd>
        </dl>
        <pre>{JSON.stringify(guide.commands || guide.example || guide, null, 2)}</pre>
      </Panel>
    </div>
  );
}
