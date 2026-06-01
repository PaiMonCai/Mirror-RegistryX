import { Badge, Panel } from '../components/common';
import type { AnyRecord } from '../types';

export function AuditLogs({ auditLogs, reload }: any) {
  const rows = auditLogs || [];

  return (
    <div className="stack">
      <Panel title="审计日志" action={<button onClick={reload}>刷新</button>}>
        <table>
          <thead><tr><th>时间</th><th>Actor</th><th>Action</th><th>资源</th><th>对象</th><th>Detail</th></tr></thead>
          <tbody>{rows.map((item: AnyRecord) => (
            <tr key={item.id}>
              <td>{item.created_at}</td>
              <td><Badge value={item.actor} /></td>
              <td><Badge value={item.action} /></td>
              <td>{item.resource_type}</td>
              <td className="mono breakable">{item.resource_id}</td>
              <td className="breakable"><pre>{JSON.stringify(item.detail || {}, null, 2)}</pre></td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>
    </div>
  );
}
