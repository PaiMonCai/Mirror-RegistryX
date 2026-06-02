import { BellRing, Boxes, Clock3, DownloadCloud, Eye, FileSearch, Play, RefreshCw, ShieldCheck } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { formatApiError } from '../api';
import { Badge, Metric, Panel } from '../components/common';
import type { AnyRecord, Mirror } from '../types';

function pretty(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}

function parseJson(value: string, fallback: unknown) {
  try {
    return value.trim() ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

const defaultTemplate = {
  id: '',
  name: '',
  source_registry_pattern: '*',
  source_namespace_pattern: '*',
  source_repo_pattern: '*',
  target_registry: 'localhost:5000',
  target_namespace_template: '{namespace}',
  mode: 'auto_push',
  check_interval_minutes: 30,
  allow_latest_push: false,
  priority: 100,
  enabled: true,
};

export function Governance({ mirrors, api, reloadMirrors, notify }: any) {
  const [summary, setSummary] = useState<AnyRecord>({});
  const [issues, setIssues] = useState<AnyRecord[]>([]);
  const [templates, setTemplates] = useState<AnyRecord[]>([]);
  const [sources, setSources] = useState<AnyRecord[]>([]);
  const [candidates, setCandidates] = useState<AnyRecord[]>([]);
  const [policies, setPolicies] = useState<AnyRecord[]>([]);
  const [windows, setWindows] = useState<AnyRecord[]>([]);
  const [bulkOps, setBulkOps] = useState<AnyRecord[]>([]);
  const [templateForm, setTemplateForm] = useState<AnyRecord>(defaultTemplate);
  const [previewSource, setPreviewSource] = useState('docker.io/library/busybox:latest');
  const [preview, setPreview] = useState<AnyRecord | null>(null);
  const [sourceForm, setSourceForm] = useState({ id: '', name: '', source_type: 'inline', location: '', content: 'image: docker.io/library/busybox:latest', scan_interval_minutes: 60, enabled: true });
  const [policyForm, setPolicyForm] = useState({ id: '', name: '', webhook_url: '', min_severity: 'warning', dedupe_seconds: 1800, quiet_hours: '{"enabled":false}', events: '{"push_failed":true,"rule_degraded":true}', enabled: true });
  const [windowForm, setWindowForm] = useState({ id: '', name: '', timezone: 'Asia/Shanghai', allow_windows: '[{"days":["mon","tue","wed","thu","fri"],"start":"09:00","end":"18:00"}]', freeze_windows: '[]', enabled: true });
  const [bulkForm, setBulkForm] = useState({ operation_type: 'check_rules', sources: '', check_interval_minutes: 30, mode: 'auto_push' });

  const pendingCandidateIds = useMemo(() => candidates.filter((item) => ['new', 'conflict'].includes(item.status)).map((item) => item.id), [candidates]);

  async function loadGovernance() {
    const [summaryRow, issueRows, templateRows, sourceRows, candidateRows, policyRows, windowRows, opRows] = await Promise.all([
      api('GET', '/governance/summary'),
      api('GET', '/governance/issues?limit=80'),
      api('GET', '/mirror-rule-templates'),
      api('GET', '/discovery-sources'),
      api('GET', '/discovery-candidates?limit=120'),
      api('GET', '/notification-policies'),
      api('GET', '/push-windows'),
      api('GET', '/bulk-operations?limit=20'),
    ]);
    setSummary(summaryRow || {});
    setIssues(issueRows || []);
    setTemplates(templateRows || []);
    setSources(sourceRows || []);
    setCandidates(candidateRows || []);
    setPolicies(policyRows || []);
    setWindows(windowRows || []);
    setBulkOps(opRows || []);
  }

  useEffect(() => {
    loadGovernance().catch((error) => notify(formatApiError(error)));
  }, []);

  async function run(label: string, fn: () => Promise<void>) {
    try {
      await fn();
      await loadGovernance();
      notify(label);
    } catch (error) {
      notify(formatApiError(error));
    }
  }

  async function saveTemplate() {
    const payload = { ...templateForm, check_interval_minutes: Number(templateForm.check_interval_minutes || 30), priority: Number(templateForm.priority || 100) };
    const result = await api('POST', '/mirror-rule-templates', payload);
    setTemplateForm({ ...defaultTemplate, id: result.template?.id || '' });
  }

  async function previewTemplate(templateId?: string) {
    const id = templateId || templateForm.id || templates[0]?.id;
    if (!id) return;
    setPreview(await api('POST', `/mirror-rule-templates/${id}/preview`, { source: previewSource }));
  }

  async function saveDiscoverySource() {
    await api('POST', '/discovery-sources', sourceForm);
  }

  async function scanSource(sourceId: string) {
    await api('POST', `/discovery-sources/${sourceId}/scan`, {});
  }

  async function importCandidates() {
    await api('POST', '/discovery-candidates/batch/import', { ids: pendingCandidateIds, trigger_sync: true });
    await reloadMirrors();
  }

  async function ignoreCandidates() {
    await api('POST', '/discovery-candidates/batch/ignore', { ids: pendingCandidateIds, reason: 'ignored from governance panel' });
  }

  async function savePolicy() {
    await api('POST', '/notification-policies', {
      ...policyForm,
      dedupe_seconds: Number(policyForm.dedupe_seconds || 1800),
      events: parseJson(policyForm.events, {}),
      quiet_hours: parseJson(policyForm.quiet_hours, {}),
    });
  }

  async function testPolicy(policyId?: string) {
    const id = policyId || policies[0]?.id;
    if (!id) return;
    await api('POST', `/notification-policies/${id}/test`, { event_type: 'push_failed', severity: 'warning', payload: { source: previewSource } });
  }

  async function saveWindow() {
    await api('POST', '/push-windows', {
      ...windowForm,
      allow_windows: parseJson(windowForm.allow_windows, []),
      freeze_windows: parseJson(windowForm.freeze_windows, []),
    });
  }

  async function previewWindow(windowId?: string) {
    const id = windowId || windows[0]?.id;
    if (!id) return;
    setPreview(await api('POST', `/push-windows/${id}/preview`, {}));
  }

  async function createBulkOperation() {
    const sources = bulkForm.sources.split('\n').map((item) => item.trim()).filter(Boolean);
    const params: AnyRecord = {};
    if (bulkForm.operation_type === 'update_interval') params.check_interval_minutes = Number(bulkForm.check_interval_minutes || 30);
    if (bulkForm.operation_type === 'update_mode') params.mode = bulkForm.mode;
    await api('POST', '/bulk-operations', { operation_type: bulkForm.operation_type, sources, params });
    await reloadMirrors();
  }

  return (
    <div className="stack governance-page">
      <div className="metric-grid">
        <Metric label="待推送" value={summary.pending_pushes ?? 0} />
        <Metric label="失败规则" value={summary.failed_rules ?? 0} />
        <Metric label="新发现" value={summary.new_discovery_candidates ?? 0} />
        <Metric label="通知失败" value={summary.notification_failures ?? 0} />
      </div>

      <div className="two-col governance-grid">
        <Panel title="治理问题" action={<button onClick={() => run('已刷新治理状态', loadGovernance)}><RefreshCw size={16} />刷新</button>}>
          <div className="activity-list compact-list">
            {issues.map((issue, index) => (
              <div className="activity-item" key={`${issue.type}-${issue.source}-${index}`}>
                <span className="act-icon"><ShieldCheck size={14} /></span>
                <div>
                  <div className="act-msg"><strong>{issue.type}</strong> {issue.source || '-'}</div>
                  <div className="act-time">{issue.message || issue.target || '-'}</div>
                </div>
                <Badge value={issue.severity || 'info'} />
              </div>
            ))}
            {!issues.length && <p className="sect-desc compact">当前没有待处理治理问题。</p>}
          </div>
        </Panel>

        <Panel title="待处理镜像">
          <div className="table-scroll short-table">
            <table>
              <thead><tr><th>Source</th><th>Status</th><th>Next</th></tr></thead>
              <tbody>
                {(mirrors || []).filter((item: Mirror) => ['pending', 'pending_window', 'failed', 'degraded'].includes(item.push_status || '')).slice(0, 8).map((item: Mirror) => (
                  <tr key={item.source}><td className="mono">{item.source}</td><td><Badge value={item.push_status} /></td><td>{item.next_push_at || '-'}</td></tr>
                ))}
                {!(mirrors || []).some((item: Mirror) => ['pending', 'pending_window', 'failed', 'degraded'].includes(item.push_status || '')) && <tr><td colSpan={3}>暂无待处理推送</td></tr>}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <div className="two-col governance-grid">
        <Panel title="规则模板" action={<button onClick={() => run('模板预览已生成', () => previewTemplate())}><Eye size={16} />预览</button>}>
          <div className="form-grid">
            <input value={templateForm.id} onChange={(event) => setTemplateForm({ ...templateForm, id: event.target.value })} placeholder="模板 ID" />
            <input value={templateForm.name} onChange={(event) => setTemplateForm({ ...templateForm, name: event.target.value })} placeholder="模板名称" />
            <input value={templateForm.target_registry} onChange={(event) => setTemplateForm({ ...templateForm, target_registry: event.target.value })} placeholder="目标 registry" />
            <input value={templateForm.target_namespace_template} onChange={(event) => setTemplateForm({ ...templateForm, target_namespace_template: event.target.value })} placeholder="命名空间模板" />
            <input value={previewSource} onChange={(event) => setPreviewSource(event.target.value)} placeholder="预览 source image" />
            <button className="primary" onClick={() => run('模板已保存', saveTemplate)}><Boxes size={16} />保存模板</button>
          </div>
          <div className="chip-list compact">{templates.map((item) => <button className="chip-button" key={item.id} onClick={() => setTemplateForm({ ...defaultTemplate, ...item })}>{item.name}</button>)}</div>
        </Panel>

        <Panel title="发现源" action={<button disabled={!pendingCandidateIds.length} onClick={() => run('候选已导入', importCandidates)}><DownloadCloud size={16} />导入候选</button>}>
          <div className="form-grid">
            <input value={sourceForm.id} onChange={(event) => setSourceForm({ ...sourceForm, id: event.target.value })} placeholder="发现源 ID" />
            <input value={sourceForm.name} onChange={(event) => setSourceForm({ ...sourceForm, name: event.target.value })} placeholder="发现源名称" />
            <select value={sourceForm.source_type} onChange={(event) => setSourceForm({ ...sourceForm, source_type: event.target.value })}>
              <option value="inline">inline</option>
              <option value="plain_text">plain_text</option>
              <option value="url">url</option>
            </select>
            <input value={sourceForm.location} onChange={(event) => setSourceForm({ ...sourceForm, location: event.target.value })} placeholder="URL location" />
            <textarea value={sourceForm.content} rows={4} onChange={(event) => setSourceForm({ ...sourceForm, content: event.target.value })} placeholder="镜像引用内容" />
            <button className="primary" onClick={() => run('发现源已保存', saveDiscoverySource)}><FileSearch size={16} />保存发现源</button>
          </div>
          <div className="row-actions">
            {sources.map((item) => <button key={item.id} onClick={() => run('扫描已完成', () => scanSource(item.id))}><Play size={14} />扫描 {item.name}</button>)}
            <button disabled={!pendingCandidateIds.length} onClick={() => run('候选已忽略', ignoreCandidates)}>忽略候选</button>
          </div>
        </Panel>
      </div>

      <div className="two-col governance-grid">
        <Panel title="通知策略" action={<button disabled={!policies.length} onClick={() => run('通知策略测试已记录', () => testPolicy())}><BellRing size={16} />测试</button>}>
          <div className="form-grid">
            <input value={policyForm.id} onChange={(event) => setPolicyForm({ ...policyForm, id: event.target.value })} placeholder="策略 ID" />
            <input value={policyForm.name} onChange={(event) => setPolicyForm({ ...policyForm, name: event.target.value })} placeholder="策略名称" />
            <input value={policyForm.webhook_url} onChange={(event) => setPolicyForm({ ...policyForm, webhook_url: event.target.value })} placeholder="Webhook URL" />
            <input value={policyForm.min_severity} onChange={(event) => setPolicyForm({ ...policyForm, min_severity: event.target.value })} placeholder="最低等级" />
            <textarea value={policyForm.events} rows={3} onChange={(event) => setPolicyForm({ ...policyForm, events: event.target.value })} placeholder="事件 JSON" />
            <textarea value={policyForm.quiet_hours} rows={3} onChange={(event) => setPolicyForm({ ...policyForm, quiet_hours: event.target.value })} placeholder="静默时间 JSON" />
            <button className="primary" onClick={() => run('通知策略已保存', savePolicy)}><BellRing size={16} />保存策略</button>
          </div>
        </Panel>

        <Panel title="推送窗口" action={<button disabled={!windows.length} onClick={() => run('窗口预览已生成', () => previewWindow())}><Clock3 size={16} />预览</button>}>
          <div className="form-grid">
            <input value={windowForm.id} onChange={(event) => setWindowForm({ ...windowForm, id: event.target.value })} placeholder="窗口 ID" />
            <input value={windowForm.name} onChange={(event) => setWindowForm({ ...windowForm, name: event.target.value })} placeholder="窗口名称" />
            <input value={windowForm.timezone} onChange={(event) => setWindowForm({ ...windowForm, timezone: event.target.value })} placeholder="时区" />
            <textarea value={windowForm.allow_windows} rows={3} onChange={(event) => setWindowForm({ ...windowForm, allow_windows: event.target.value })} placeholder="允许窗口 JSON" />
            <textarea value={windowForm.freeze_windows} rows={3} onChange={(event) => setWindowForm({ ...windowForm, freeze_windows: event.target.value })} placeholder="冻结窗口 JSON" />
            <button className="primary" onClick={() => run('推送窗口已保存', saveWindow)}><Clock3 size={16} />保存窗口</button>
          </div>
        </Panel>
      </div>

      <Panel title="批量操作" action={<button className="primary" onClick={() => run('批量操作已创建', createBulkOperation)}><Play size={16} />执行</button>}>
        <div className="form-grid governance-bulk-form">
          <select value={bulkForm.operation_type} onChange={(event) => setBulkForm({ ...bulkForm, operation_type: event.target.value })}>
            <option value="check_rules">检查规则</option>
            <option value="push_pending">推送待处理</option>
            <option value="pause_rules">暂停规则</option>
            <option value="resume_rules">恢复规则</option>
            <option value="update_interval">更新间隔</option>
            <option value="update_mode">更新模式</option>
          </select>
          <input type="number" value={bulkForm.check_interval_minutes} onChange={(event) => setBulkForm({ ...bulkForm, check_interval_minutes: Number(event.target.value || 30) })} placeholder="检查间隔" />
          <select value={bulkForm.mode} onChange={(event) => setBulkForm({ ...bulkForm, mode: event.target.value })}>
            <option value="auto_push">auto_push</option>
            <option value="monitor_only">monitor_only</option>
          </select>
          <textarea value={bulkForm.sources} rows={4} onChange={(event) => setBulkForm({ ...bulkForm, sources: event.target.value })} placeholder="可选：每行一个 source；留空表示全部规则" />
        </div>
        <div className="table-scroll short-table">
          <table>
            <thead><tr><th>ID</th><th>类型</th><th>状态</th><th>成功</th><th>失败</th></tr></thead>
            <tbody>
              {bulkOps.map((item) => <tr key={item.id}><td>#{item.id}</td><td>{item.operation_type}</td><td><Badge value={item.status} /></td><td>{item.succeeded}</td><td>{item.failed}</td></tr>)}
              {!bulkOps.length && <tr><td colSpan={5}>暂无批量操作</td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>

      {preview && (
        <Panel title="预览结果">
          <pre>{pretty(preview)}</pre>
        </Panel>
      )}
    </div>
  );
}
