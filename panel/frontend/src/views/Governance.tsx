import { useState } from 'react';
import { Badge, Metric, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord } from '../types';

export function Governance({ governance, api, reload, notify }: any) {
  const [rule, setRule] = useState({ id: '', name: '', repo_pattern: 'library/*', tag_pattern: 'v*', environment: '*', reason: '' });
  const [policy, setPolicy] = useState({ id: '', name: '', repo_pattern: 'library/*', environment: '*', keep_last: 3, max_age_days: '' });
  const [drill, setDrill] = useState<AnyRecord | null>(null);
  const [migration, setMigration] = useState<AnyRecord | null>(null);
  const rules = governance.tagProtection || [];
  const policies = governance.retentionPolicies || [];
  const guide = governance.backupRestoreGuide || {};
  const migrationPlan = governance.migrationPlan || {};

  async function saveRule() {
    await api('POST', '/tag-protection', { ...rule, enabled: true });
    setRule({ id: '', name: '', repo_pattern: 'library/*', tag_pattern: 'v*', environment: '*', reason: '' });
    await reload();
    notify('保护规则已保存');
  }

  async function savePolicy() {
    await api('POST', '/retention-policies', {
      ...policy,
      keep_last: Number(policy.keep_last) || 1,
      max_age_days: policy.max_age_days === '' ? null : Number(policy.max_age_days),
      enabled: true,
    });
    setPolicy({ id: '', name: '', repo_pattern: 'library/*', environment: '*', keep_last: 3, max_age_days: '' });
    await reload();
    notify('保留策略已保存');
  }

  async function runRestoreDrill() {
    const result = await api('POST', '/backup-restore/drill', { require_credentials_secret: true, verify_registry_sample: false });
    setDrill(result);
    notify('恢复演练已完成');
  }

  async function runMigrationPreflight() {
    const result = await api('POST', '/migration/preflight', {});
    setMigration(result);
    notify('迁移预检已完成');
  }

  return (
    <div className="stack">
      <div className="metric-grid">
        <Metric label="Tag 保护" value={rules.length} />
        <Metric label="保留策略" value={policies.length} />
        <Metric label="恢复演练" value={drill ? <Badge value={drill.summary?.status || (drill.ok ? 'ok' : 'warn')} /> : '未执行'} />
        <Metric label="迁移预检" value={migration ? <Badge value={migration.summary?.status || (migration.ok ? 'ok' : 'warn')} /> : '未执行'} />
      </div>

      <Panel title="仓库治理">
        <div className="form-grid">
          <input placeholder="规则 ID" value={rule.id} onChange={(event) => setRule({ ...rule, id: event.target.value })} />
          <input placeholder="规则名称" value={rule.name} onChange={(event) => setRule({ ...rule, name: event.target.value })} />
          <input placeholder="repo pattern" value={rule.repo_pattern} onChange={(event) => setRule({ ...rule, repo_pattern: event.target.value })} />
          <input placeholder="tag pattern" value={rule.tag_pattern} onChange={(event) => setRule({ ...rule, tag_pattern: event.target.value })} />
          <input placeholder="environment" value={rule.environment} onChange={(event) => setRule({ ...rule, environment: event.target.value })} />
          <input placeholder="原因" value={rule.reason} onChange={(event) => setRule({ ...rule, reason: event.target.value })} />
          <button className="primary" onClick={saveRule}>保存保护规则</button>
        </div>
      </Panel>

      <Panel title="Tag 保护规则" action={<button onClick={reload}>刷新</button>}>
        <table>
          <thead><tr><th>ID</th><th>名称</th><th>仓库</th><th>Tag</th><th>环境</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{rules.map((item: AnyRecord) => (
            <tr key={item.id}>
              <td className="mono">{item.id}</td>
              <td>{item.name}</td>
              <td className="mono breakable">{item.repo_pattern}</td>
              <td className="mono">{item.tag_pattern}</td>
              <td>{item.environment}</td>
              <td><Badge value={item.enabled ? 'enabled' : 'disabled'} /></td>
              <td><ConfirmButton confirmText="确认删除保护规则" onConfirm={() => api('DELETE', `/tag-protection/${item.id}`).then(reload)}>删除</ConfirmButton></td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>

      <Panel title="保留策略">
        <div className="form-grid">
          <input placeholder="策略 ID" value={policy.id} onChange={(event) => setPolicy({ ...policy, id: event.target.value })} />
          <input placeholder="策略名称" value={policy.name} onChange={(event) => setPolicy({ ...policy, name: event.target.value })} />
          <input placeholder="repo pattern" value={policy.repo_pattern} onChange={(event) => setPolicy({ ...policy, repo_pattern: event.target.value })} />
          <input placeholder="environment" value={policy.environment} onChange={(event) => setPolicy({ ...policy, environment: event.target.value })} />
          <input type="number" placeholder="keep_last" value={policy.keep_last} onChange={(event) => setPolicy({ ...policy, keep_last: Number(event.target.value) })} />
          <input type="number" placeholder="max_age_days" value={policy.max_age_days} onChange={(event) => setPolicy({ ...policy, max_age_days: event.target.value })} />
          <button className="primary" onClick={savePolicy}>保存保留策略</button>
        </div>
        <table>
          <thead><tr><th>ID</th><th>仓库</th><th>保留</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{policies.map((item: AnyRecord) => (
            <tr key={item.id}>
              <td className="mono">{item.id}</td>
              <td className="mono breakable">{item.repo_pattern}</td>
              <td>{item.keep_last}</td>
              <td><Badge value={item.enabled ? 'enabled' : 'disabled'} /></td>
              <td className="row-actions">
                <button onClick={() => api('POST', `/retention-policies/${item.id}/dry-run`, {}).then((result: AnyRecord) => notify(`dry-run 候选 ${result.candidates?.length || 0}`))}>Dry-run</button>
                <ConfirmButton confirmText="确认应用保留策略" onConfirm={() => api('POST', `/retention-policies/${item.id}/apply`, {}).then(() => { reload(); notify('保留策略已应用'); })}>应用</ConfirmButton>
              </td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>

      <Panel title="备份恢复与恢复演练" action={<button onClick={runRestoreDrill}>恢复演练</button>}>
        <dl className="kv">
          <dt>必备项目</dt><dd>{(guide.required_items || []).join(', ') || '-'}</dd>
          <dt>命令清单</dt><dd>{(guide.backup_commands || []).join(' | ') || '-'}</dd>
          <dt>只读验证</dt><dd>{(guide.readonly_verification || []).join(' | ') || '-'}</dd>
        </dl>
        {drill && <pre>{JSON.stringify(drill.report || drill.summary || drill, null, 2)}</pre>}
      </Panel>

      <Panel title="迁移恢复向导" action={<button onClick={runMigrationPreflight}>迁移预检</button>}>
        <dl className="kv">
          <dt>迁移计划</dt><dd>{migrationPlan.title || '跨机器迁移计划'}</dd>
          <dt>源端报告</dt><dd className="mono breakable">{migrationPlan.commands?.source_report || '-'}</dd>
          <dt>备份清单</dt><dd>{(migrationPlan.manifest?.required_items || []).map((item: AnyRecord) => item.name || item).join(', ') || '-'}</dd>
        </dl>
        {migration && <pre>{JSON.stringify(migration.summary || migration, null, 2)}</pre>}
      </Panel>
    </div>
  );
}
