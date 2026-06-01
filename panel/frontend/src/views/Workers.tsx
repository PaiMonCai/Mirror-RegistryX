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
import { Badge, Metric, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord, View, Mirror, Credential } from '../types';
import { cx, diagnosticMessage, formatMB, formatRate, hostFromImage } from '../utils';

export function Workers({ workers, guide, reload }: any) {
  return (
    <div className="stack">
      <Panel title="Worker 状态" action={<button onClick={reload}><RefreshCw size={16} />刷新</button>}>
        <table><thead><tr><th>ID</th><th>名称</th><th>环境</th><th>状态</th><th>标签</th><th>能力</th><th>心跳</th><th>最近任务</th><th>消息</th></tr></thead>
          <tbody>{workers.map((worker: AnyRecord) => <tr key={worker.worker_id}><td className="mono">{worker.worker_id}</td><td>{worker.name}</td><td>{worker.environment}</td><td><Badge value={worker.status} /></td><td>{(worker.labels || []).join(', ') || '-'}</td><td>{(worker.capabilities || []).join(', ') || '-'}</td><td>{worker.last_heartbeat}</td><td>{worker.latest_claim ? `#${worker.latest_claim.queue_id} ${worker.latest_claim.status}` : '-'}</td><td>{worker.message || '-'}</td></tr>)}</tbody>
        </table>
      </Panel>
      <Panel title="Worker 接入">
        <dl className="kv">
          <dt>WORKER_TOKEN</dt><dd>{guide.enabled ? '已配置' : '未配置'}</dd>
          <dt>心跳</dt><dd>{guide.endpoints?.heartbeat || '-'}</dd>
          <dt>领取</dt><dd>{guide.endpoints?.claim || '-'}</dd>
          <dt>回写</dt><dd>{guide.endpoints?.complete || '-'}</dd>
        </dl>
        <pre>{JSON.stringify(guide.notes || [], null, 2)}</pre>
      </Panel>
    </div>
  );
}
