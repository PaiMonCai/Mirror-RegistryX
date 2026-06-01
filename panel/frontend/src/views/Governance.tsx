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

export function Governance({ governance, api, reload, notify }: any) {
  const [rule, setRule] = useState({ id: '', name: '', repo_pattern: '*', tag_pattern: 'v*', environment: '*', enabled: true, reason: 'release tag' });
  const [policy, setPolicy] = useState({ id: '', name: '', repo_pattern: '*', environment: '*', keep_last: 5, max_age_days: 30, enabled: false });
  const [dryRun, setDryRun] = useState<AnyRecord | null>(null);
  const [restoreDrill, setRestoreDrill] = useState<AnyRecord | null>(null);
  const [migrationPreflight, setMigrationPreflight] = useState<AnyRecord | null>(null);
  async function saveRule() {
    await api('POST', '/tag-protection', { ...rule, id: rule.id || undefined });
    await reload();
    notify('保护规则已保存');
  }
  async function savePolicy() {
    await api('POST', '/retention-policies', { ...policy, id: policy.id || undefined });
    await reload();
    notify('保留策略已保存');
  }
  async function runPolicy(id: string, apply = false) {
    const result = await api('POST', `/retention-policies/${id}/${apply ? 'apply' : 'dry-run'}`, {});
    setDryRun(result);
    await reload();
    notify(apply ? '保留策略已标记候选 tag' : 'dry-run 已完成');
  }
  async function runRestoreDrill() {
    const result = await api('POST', '/backup-restore/drill', { require_credentials_secret: true, verify_registry_sample: false });
    setRestoreDrill(result);
    notify(`恢复演练: ${result.summary.status}`);
  }
  async function runMigrationPreflight() {
    const result = await api('POST', '/migration/preflight', {});
    setMigrationPreflight(result);
    notify(`迁移预检: ${result.summary.status}`);
  }
  return (
    <div className="stack">
      <Panel title="Tag 保护规则">
        <div className="form-grid">
          <input placeholder="id" value={rule.id} onChange={(e) => setRule({ ...rule, id: e.target.value })} />
          <input placeholder="名称" value={rule.name} onChange={(e) => setRule({ ...rule, name: e.target.value })} />
          <input placeholder="repo pattern" value={rule.repo_pattern} onChange={(e) => setRule({ ...rule, repo_pattern: e.target.value })} />
          <input placeholder="tag pattern" value={rule.tag_pattern} onChange={(e) => setRule({ ...rule, tag_pattern: e.target.value })} />
          <input placeholder="environment" value={rule.environment} onChange={(e) => setRule({ ...rule, environment: e.target.value })} />
          <input placeholder="原因" value={rule.reason} onChange={(e) => setRule({ ...rule, reason: e.target.value })} />
          <button className="primary" onClick={saveRule}>保存规则</button>
        </div>
        <table><thead><tr><th>ID</th><th>Repo</th><th>Tag</th><th>环境</th><th>状态</th></tr></thead><tbody>{(governance.rules || []).map((item: AnyRecord) => <tr key={item.id}><td>{item.id}</td><td>{item.repo_pattern}</td><td>{item.tag_pattern}</td><td>{item.environment}</td><td><Badge value={item.enabled ? 'enabled' : 'disabled'} /></td></tr>)}</tbody></table>
      </Panel>
      <Panel title="保留策略">
        <div className="form-grid">
          <input placeholder="id" value={policy.id} onChange={(e) => setPolicy({ ...policy, id: e.target.value })} />
          <input placeholder="名称" value={policy.name} onChange={(e) => setPolicy({ ...policy, name: e.target.value })} />
          <input placeholder="repo pattern" value={policy.repo_pattern} onChange={(e) => setPolicy({ ...policy, repo_pattern: e.target.value })} />
          <input placeholder="environment" value={policy.environment} onChange={(e) => setPolicy({ ...policy, environment: e.target.value })} />
          <input type="number" value={policy.keep_last} onChange={(e) => setPolicy({ ...policy, keep_last: Number(e.target.value) })} />
          <input type="number" value={policy.max_age_days} onChange={(e) => setPolicy({ ...policy, max_age_days: Number(e.target.value) })} />
          <button className="primary" onClick={savePolicy}>保存策略</button>
        </div>
        <table><thead><tr><th>ID</th><th>Repo</th><th>保留</th><th>天数</th><th>状态</th><th>操作</th></tr></thead><tbody>{(governance.policies || []).map((item: AnyRecord) => <tr key={item.id}><td>{item.id}</td><td>{item.repo_pattern}</td><td>{item.keep_last}</td><td>{item.max_age_days || '-'}</td><td><Badge value={item.enabled ? 'enabled' : 'dry-run'} /></td><td className="row-actions"><button onClick={() => runPolicy(item.id)}>dry-run</button><ConfirmButton confirmText="确认标记" className="" onConfirm={() => runPolicy(item.id, true)}>标记</ConfirmButton></td></tr>)}</tbody></table>
        {dryRun && <pre>{JSON.stringify(dryRun, null, 2)}</pre>}
      </Panel>
      <Panel title="备份恢复清单" action={<button onClick={runRestoreDrill}><ListChecks size={16} />恢复演练</button>}>
        <pre>{JSON.stringify(governance.backup || {}, null, 2)}</pre>
        {restoreDrill && <pre>{JSON.stringify(restoreDrill, null, 2)}</pre>}
      </Panel>
      <Panel title="迁移恢复向导" action={<button onClick={runMigrationPreflight}><ListChecks size={16} />迁移预检</button>}>
        <div className="chip-list">
          <span className="chip">配置 {governance.migration?.manifest?.source?.app_version || '-'}</span>
          <span className="chip">镜像 {governance.migration?.manifest?.source?.image_tag || '-'}</span>
          <span className="chip">队列 {governance.migration?.manifest?.queue?.active ?? 0}</span>
        </div>
        {migrationPreflight ? (
          <table><thead><tr><th>检查项</th><th>状态</th><th>结果</th><th>建议</th></tr></thead>
            <tbody>{(migrationPreflight.checks || []).map((item: AnyRecord) => <tr key={item.name}><td>{item.name}</td><td><Badge value={item.status} /></td><td>{item.message}</td><td>{item.suggestion || '-'}</td></tr>)}</tbody>
          </table>
        ) : (
          <table><thead><tr><th>阶段</th><th>步骤</th></tr></thead>
            <tbody>{(governance.migration?.restore_steps || []).map((step: string, index: number) => <tr key={step}><td>{index + 1}</td><td>{step}</td></tr>)}</tbody>
          </table>
        )}
        <pre>{JSON.stringify(governance.migration?.commands || {}, null, 2)}</pre>
      </Panel>
    </div>
  );
}
