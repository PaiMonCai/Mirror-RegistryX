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

export function Storage({ storage, api, reload, notify }: any) {
  async function recalculate() {
    await api('POST', '/storage/stats/recalculate', {});
    notify('体积统计重算已排队');
  }
  const rows = (storage.images || []).flatMap((image: AnyRecord) =>
    (image.tags || []).map((tag: AnyRecord) => ({
      image,
      tag,
      logical: tag.stats?.logical_size_bytes,
      deduped: tag.stats?.deduplicated_size_bytes ?? image.deduplicated_size_bytes ?? image.estimated_size_bytes,
    })),
  );
  return (
    <div className="stack">
      <div className="metric-grid storage-summary">
        <Metric label="估算总占用" value={formatMB(storage.estimated_total_bytes)} />
        <Metric label="物理 blob" value={formatMB(storage.physical_blob_bytes)} />
        <Metric label="镜像仓库" value={(storage.images || []).length} />
      </div>
      <Panel title="本地仓库" action={<button onClick={recalculate}>重算体积</button>}>
        <table><thead><tr><th>仓库</th><th>Tag</th><th className="num">逻辑体积</th><th className="num">去重体积</th><th className="num">共享层</th><th>删除标记</th></tr></thead>
          <tbody>{rows.map(({ image, tag, logical, deduped }: AnyRecord) => <tr key={`${image.repo}:${tag.name}`}><td className="breakable mono">{image.repo}</td><td className="breakable mono">{tag.name}</td><td className="num">{formatMB(logical)}</td><td className="num">{formatMB(deduped)}</td><td className="num">{tag.stats?.shared_blob_count ?? '-'}</td><td>{tag.marked_for_deletion ? '已标记' : <ConfirmButton confirmText="确认标记" className="" onConfirm={() => api('POST', '/storage/delete-mark', { repo: image.repo, tag: tag.name, reason: 'manual' }).then(() => { reload(); notify('已标记'); })}>标记</ConfirmButton>}</td></tr>)}</tbody>
        </table>
      </Panel>
      <Panel title="垃圾回收指引"><pre>{(storage.garbage_collection?.commands || []).join('\n')}</pre></Panel>
    </div>
  );
}
