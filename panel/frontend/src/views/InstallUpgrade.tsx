import { useState } from 'react';
import { Badge, Metric, Panel } from '../components/common';
import type { AnyRecord } from '../types';

export function InstallUpgrade({ installUpgrade, api, reload, notify }: any) {
  const [expectedTag, setExpectedTag] = useState('');
  const [previousTag, setPreviousTag] = useState('');
  const [preflight, setPreflight] = useState<AnyRecord | null>(null);
  const guide = installUpgrade.guide || {};
  const checklist = installUpgrade.checklist || {};

  async function runPreflight() {
    const result = await api('POST', '/install-upgrade/preflight', {
      expected_tag: expectedTag || undefined,
      previous_tag: previousTag || undefined,
    });
    setPreflight(result);
    notify('升级预检已完成');
  }

  const checks = preflight?.checks || checklist.checks || [];

  return (
    <div className="stack">
      <div className="metric-grid">
        <Metric label="安装升级" value={guide.readonly ? <Badge value="readonly" /> : <Badge value="ready" />} />
        <Metric label="升级预检" value={preflight ? <Badge value={preflight.summary?.status || (preflight.ok ? 'ok' : 'warn')} /> : '未执行'} />
        <Metric label="设置检查" value={checks.length} />
        <Metric label="镜像 tag" value={guide.runtime?.image_tag || '-'} />
      </div>

      <Panel title="安装升级" action={<button onClick={reload}>刷新</button>}>
        <dl className="kv">
          <dt>脚本</dt><dd className="mono breakable">{guide.host_script || 'scripts\\upgrade-check.ps1'}</dd>
          <dt>必备项目</dt><dd>{(guide.required_items || []).join(', ') || '-'}</dd>
          <dt>运行版本</dt><dd>{guide.runtime?.app_version || '-'}</dd>
          <dt>数据卷</dt><dd>{(guide.volumes || []).join(', ') || '-'}</dd>
        </dl>
      </Panel>

      <Panel title="升级预检">
        <div className="form-grid">
          <input placeholder="期望镜像 tag" value={expectedTag} onChange={(event) => setExpectedTag(event.target.value)} />
          <input placeholder="回滚 tag" value={previousTag} onChange={(event) => setPreviousTag(event.target.value)} />
          <button className="primary" onClick={runPreflight}>运行升级预检</button>
        </div>
        <div className="check-grid">
          {checks.map((item: AnyRecord) => (
            <div className="check" key={item.name}>
              <div><Badge value={item.status || (item.ok ? 'ok' : 'warn')} /> <strong>{item.name}</strong></div>
              <span>{item.message || item.path || '-'}</span>
              <small>{item.suggestion || ''}</small>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="命令清单">
        <pre>{JSON.stringify(preflight?.commands || guide.commands || guide.steps || {}, null, 2)}</pre>
      </Panel>
    </div>
  );
}
