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

export function Runs({ runs, syncQueue, selectedRun, setSelectedRun, api, reload, notify }: any) {
  async function openRun(id: number) {
    setSelectedRun(await api('GET', `/sync-runs/${id}`));
  }
  async function queueAction(id: number, action: string, label: string) {
    await api('POST', `/sync-queue/${id}/${action}`, {});
    await reload();
    notify(label);
  }
  const queueRows = syncQueue || [];
  return (
    <div className="stack">
      <Panel title="同步队列" action={<button onClick={reload}><RefreshCw size={16} />刷新</button>}>
        {queueRows.length === 0 ? <p className="warn">当前没有同步队列任务。</p> : (
          <table><thead><tr><th>ID</th><th>原因</th><th>状态</th><th>镜像</th><th>优先级</th><th>尝试</th><th>Run</th><th>消息</th><th>时间</th><th>操作</th></tr></thead>
            <tbody>{queueRows.map((task: AnyRecord) => {
              const status = String(task.status || '');
              const terminal = ['completed', 'failed', 'canceled'].includes(status);
              return (
                <tr key={task.id}>
                  <td>{task.id}</td>
                  <td>{task.reason}</td>
                  <td><Badge value={status} /></td>
                  <td className="breakable mono">{(task.sources || []).join(', ') || '全部镜像'}</td>
                  <td>{task.priority}</td>
                  <td>{task.attempts}</td>
                  <td>{task.run_id ? <button onClick={() => openRun(task.run_id)}>#{task.run_id}</button> : '-'}</td>
                  <td>{task.message || '-'}</td>
                  <td>{task.started_at || task.scheduled_at || task.created_at}</td>
                  <td className="row-actions">
                    {status === 'queued' && <button onClick={() => queueAction(task.id, 'pause', '队列任务已暂停')}><Pause size={14} />暂停</button>}
                    {status === 'paused' && <button onClick={() => queueAction(task.id, 'resume', '队列任务已恢复')}><Play size={14} />恢复</button>}
                    {['queued', 'paused', 'running'].includes(status) && <ConfirmButton confirmText="确认取消" className="" onConfirm={() => queueAction(task.id, 'cancel', '队列任务已取消')}><XCircle size={14} />取消</ConfirmButton>}
                    {terminal && <button onClick={() => queueAction(task.id, 'replay', '队列任务已重放')}><RotateCcw size={14} />重放</button>}
                  </td>
                </tr>
              );
            })}</tbody>
          </table>
        )}
      </Panel>
      <Panel title="任务历史" action={<button onClick={reload}><RefreshCw size={16} />刷新</button>}>
        <table><thead><tr><th>ID</th><th>原因</th><th>状态</th><th>更新</th><th>失败</th><th>时间</th><th></th></tr></thead>
          <tbody>{runs.map((run: AnyRecord) => <tr key={run.id}><td>{run.id}</td><td>{run.reason}</td><td><Badge value={run.status} /></td><td>{run.updated}</td><td>{run.failed}</td><td>{run.started_at}</td><td><button onClick={() => openRun(run.id)}>详情</button></td></tr>)}</tbody>
        </table>
      </Panel>
      {selectedRun && <Panel title={`任务 ${selectedRun.run.id}`}>
        <table><thead><tr><th>镜像</th><th>目标</th><th>状态</th><th>阶段</th><th>错误</th><th></th></tr></thead>
          <tbody>{selectedRun.items.map((item: AnyRecord) => <tr key={item.id}><td>{item.source}</td><td>{item.target}</td><td><Badge value={item.status} /></td><td>{item.step}</td><td>{item.error}</td><td>{item.status === 'failed' && <button onClick={() => api('POST', `/sync-run-items/${item.id}/retry`).then(() => { reload(); notify('失败项已入队'); })}>重试</button>}</td></tr>)}</tbody>
        </table>
      </Panel>}
    </div>
  );
}
