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

export function Storage({ storage, api, reload, notify }: any) {
  const [applyingMarkId, setApplyingMarkId] = useState<number | null>(null);

  async function recalculate() {
    try {
      await api('POST', '/storage/stats/recalculate', {});
      notify('体积统计重算已排队');
    } catch (error) {
      notify(formatApiError(error));
    }
  }

  async function markForDelete(image: AnyRecord, tag: AnyRecord) {
    try {
      await api('POST', '/storage/delete-mark', { repo: image.repo, tag: tag.name, reason: 'manual' });
      await reload();
      notify('已标记');
    } catch (error) {
      notify(formatApiError(error));
    }
  }

  async function applyDeleteMark(mark: AnyRecord | null | undefined) {
    const markId = Number(mark?.id);
    if (!Number.isFinite(markId) || markId <= 0) {
      notify('删除标记缺少 ID，无法执行清理');
      return;
    }
    setApplyingMarkId(markId);
    try {
      const result = await api('POST', `/storage/delete-mark/${markId}/apply`);
      await reload();
      notify(`manifest 已删除：${result.repo}:${result.tag}`);
    } catch (error) {
      notify(formatApiError(error));
    } finally {
      setApplyingMarkId(null);
    }
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
        <table><thead><tr><th>仓库</th><th>Tag</th><th className="num">逻辑体积</th><th className="num">去重体积</th><th className="num">共享层</th><th>删除标记 / 清理</th></tr></thead>
          <tbody>{rows.map(({ image, tag, logical, deduped }: AnyRecord) => {
            const markId = Number(tag.deletion_mark?.id);
            const applying = applyingMarkId === markId;
            return (
              <tr key={`${image.repo}:${tag.name}`}>
                <td className="breakable mono">{image.repo}</td>
                <td className="breakable mono">{tag.name}</td>
                <td className="num">{formatMB(logical)}</td>
                <td className="num">{formatMB(deduped)}</td>
                <td className="num">{tag.stats?.shared_blob_count ?? '-'}</td>
                <td>
                  {tag.marked_for_deletion ? (
                    <div className="row-actions storage-actions">
                      <span className="badge pending">已标记</span>
                      <ConfirmButton
                        confirmText="确认清理"
                        className="danger"
                        disabled={applying}
                        onConfirm={() => applyDeleteMark(tag.deletion_mark)}
                      >
                        {applying ? '清理中' : '执行清理'}
                      </ConfirmButton>
                    </div>
                  ) : (
                    <ConfirmButton confirmText="确认标记" className="" onConfirm={() => markForDelete(image, tag)}>标记</ConfirmButton>
                  )}
                </td>
              </tr>
            );
          })}</tbody>
        </table>
      </Panel>
      <Panel title="垃圾回收指引">
        <p className="sect-desc compact">{storage.garbage_collection?.summary}</p>
        <pre>{(storage.garbage_collection?.commands || []).join('\n')}</pre>
      </Panel>
    </div>
  );
}
