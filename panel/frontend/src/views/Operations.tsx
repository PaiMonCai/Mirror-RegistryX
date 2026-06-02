import { Activity, CheckCircle2, ClipboardList, RefreshCw, RotateCcw, ShieldCheck, UploadCloud, Wrench } from 'lucide-react';
import { formatApiError } from '../api';
import { Badge, Metric, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord } from '../types';

const SERVICES = ['panel', 'sync', 'registry', 'ops-agent'];

function taskTitle(task: AnyRecord) {
  if (!task) return '-';
  if (task.action === 'restart_service') return `restart ${task.params?.service || '-'}`;
  if (task.action === 'update_services') return `update ${(task.params?.services || []).join(', ') || 'standard'}`;
  return task.action || '-';
}

export function Operations({ agents, tasks, events, selectedTask, setSelectedTask, api, reload, notify }: any) {
  const onlineAgents = (agents || []).filter((agent: AnyRecord) => agent.status === 'online');
  const activeTasks = (tasks || []).filter((task: AnyRecord) => ['queued', 'claimed', 'running'].includes(task.status));
  const latestFailure = (tasks || []).find((task: AnyRecord) => ['failed', 'timed_out'].includes(task.status));

  async function createTask(action: string, params: AnyRecord = {}, autoConfirm = false) {
    try {
      const result = await api('POST', '/ops-tasks', { action, params });
      const task = result.task;
      if (autoConfirm && task?.requires_confirmation) {
        await api('POST', `/ops-tasks/${task.id}/confirm`, {});
      }
      await reload(task?.id);
      notify(`运维任务已创建：#${task?.id || '-'}`);
    } catch (error) {
      notify(formatApiError(error));
    }
  }

  async function confirmTask(task: AnyRecord) {
    try {
      const result = await api('POST', `/ops-tasks/${task.id}/confirm`, {});
      await reload(result.task?.id || task.id);
      notify(`已确认任务 #${task.id}`);
    } catch (error) {
      notify(formatApiError(error));
    }
  }

  async function cancelTask(task: AnyRecord) {
    try {
      await api('POST', `/ops-tasks/${task.id}/cancel`, {});
      await reload(task.id);
      notify(`已取消任务 #${task.id}`);
    } catch (error) {
      notify(formatApiError(error));
    }
  }

  return (
    <div className="stack">
      <div className="metric-grid ops-summary">
        <Metric label="在线代理" value={`${onlineAgents.length}/${(agents || []).length}`} />
        <Metric label="活跃任务" value={activeTasks.length} />
        <Metric label="最近任务" value={tasks?.[0] ? <Badge value={tasks[0].status} /> : '-'} />
        <Metric label="最近失败" value={latestFailure ? `#${latestFailure.id}` : '-'} />
      </div>

      <div className="two-col ops-grid">
        <Panel title="代理状态" action={<button onClick={() => reload(selectedTask?.id)}><RefreshCw size={16} />刷新</button>}>
          <div className="check-grid">
            {(agents || []).map((agent: AnyRecord) => (
              <div className="check" key={agent.agent_id}>
                <div className="check-status"><Badge value={agent.status} /></div>
                <strong>{agent.host_label || agent.agent_id}</strong>
                <small>{agent.agent_id} · {agent.environment || 'prod'} · {agent.last_heartbeat_at || '-'}</small>
                <div className="chip-list compact">
                  {(agent.capabilities || []).map((item: string) => <span className="chip" key={item}>{item}</span>)}
                </div>
              </div>
            ))}
            {!(agents || []).length && <p className="sect-desc compact">暂无 ops-agent 心跳。确认 Compose 中已配置 OPS_AGENT_TOKEN 并启动 ops-agent。</p>}
          </div>
        </Panel>

        <Panel title="快捷操作">
          <div className="ops-actions">
            <button onClick={() => createTask('service_status')}><Activity size={16} />服务状态</button>
            <ConfirmButton className="primary" confirmText="确认更新服务" onConfirm={() => createTask('update_services', { services: ['panel', 'sync', 'registry'] }, true)}>
              <UploadCloud size={16} />一键更新
            </ConfirmButton>
            <button onClick={() => createTask('backup_verify')}><ShieldCheck size={16} />备份检查</button>
            <button onClick={() => createTask('diagnostic_bundle')}><ClipboardList size={16} />诊断包</button>
          </div>
          <div className="ops-service-grid">
            {SERVICES.map((service) => (
              <ConfirmButton
                key={service}
                confirmText={`确认重启 ${service}`}
                className={service === 'registry' || service === 'ops-agent' ? 'danger' : ''}
                onConfirm={() => createTask('restart_service', { service }, service === 'registry' || service === 'ops-agent')}
              >
                <RotateCcw size={14} />{service}
              </ConfirmButton>
            ))}
          </div>
        </Panel>
      </div>

      <Panel title="运维任务">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>动作</th>
                <th>状态</th>
                <th>代理</th>
                <th>创建</th>
                <th>确认</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(tasks || []).map((task: AnyRecord) => (
                <tr key={task.id}>
                  <td className="mono">#{task.id}</td>
                  <td>{taskTitle(task)}</td>
                  <td><Badge value={task.status} /></td>
                  <td>{task.agent_id || '-'}</td>
                  <td>{task.created_at || '-'}</td>
                  <td>{task.requires_confirmation ? <Badge value="pending" /> : (task.confirmed_at || '-')}</td>
                  <td>
                    <div className="row-actions">
                      <button onClick={() => setSelectedTask(task)}><Wrench size={14} />详情</button>
                      {task.requires_confirmation && <ConfirmButton className="primary" confirmText="确认执行" onConfirm={() => confirmTask(task)}>确认</ConfirmButton>}
                      {['queued', 'claimed', 'running'].includes(task.status) && <ConfirmButton className="danger" confirmText="确认取消" onConfirm={() => cancelTask(task)}>取消</ConfirmButton>}
                    </div>
                  </td>
                </tr>
              ))}
              {!(tasks || []).length && <tr><td colSpan={7}>暂无运维任务</td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>

      {selectedTask && (
        <Panel title={`任务详情 #${selectedTask.id}`} action={<button onClick={() => reload(selectedTask.id)}><RefreshCw size={16} />刷新</button>}>
          <dl className="kv">
            <dt>动作</dt><dd>{taskTitle(selectedTask)}</dd>
            <dt>状态</dt><dd><Badge value={selectedTask.status} /></dd>
            <dt>退出码</dt><dd>{selectedTask.exit_code ?? '-'}</dd>
            <dt>错误</dt><dd>{selectedTask.error || '-'}</dd>
          </dl>
          {selectedTask.log_tail && <pre>{selectedTask.log_tail}</pre>}
          <div className="activity-list">
            {(events || []).map((event: AnyRecord) => (
              <div className="activity-item" key={event.id}>
                <span className="act-icon"><CheckCircle2 size={14} /></span>
                <div>
                  <div className="act-msg"><strong>{event.type}</strong> {event.message || ''}</div>
                  <div className="act-time">{event.created_at}</div>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
