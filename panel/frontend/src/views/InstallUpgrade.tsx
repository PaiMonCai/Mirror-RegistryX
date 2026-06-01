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

export function InstallUpgrade({ guide, api, reload, notify }: any) {
  const [form, setForm] = useState({ expected_tag: '', previous_tag: '' });
  const [preflight, setPreflight] = useState<AnyRecord | null>(null);
  useEffect(() => {
    setForm((current) => current.expected_tag ? current : { ...current, expected_tag: guide.runtime?.image_tag || '' });
  }, [guide.runtime?.image_tag]);
  async function runPreflight() {
    const result = await api('POST', '/install-upgrade/preflight', {
      expected_tag: form.expected_tag || undefined,
      previous_tag: form.previous_tag || undefined,
    });
    setPreflight(result);
    notify(`升级预检: ${result.summary.status}`);
  }
  const runtime = guide.runtime || {};
  const result = preflight || guide.preflight || {};
  const cards = [
    ['APP', runtime.app_version || '-'],
    ['镜像 tag', runtime.image_tag || '-'],
    ['数据库', runtime.database_backend || '-'],
    ['队列', runtime.active_queue ?? 0],
    ['管理员', <Badge value={runtime.admin_initialized ? 'ok' : 'error'} />],
    ['密钥', <Badge value={runtime.credentials_secret_configured ? 'ok' : 'warn'} />],
  ];
  return (
    <div className="stack">
      <div className="metric-grid">{cards.map(([label, value]) => <Metric key={label} label={label as string} value={value} />)}</div>
      <Panel title="安装升级预检" action={<button onClick={reload}><RefreshCw size={16} />刷新</button>}>
        <div className="form-grid">
          <input value={form.expected_tag} onChange={(e) => setForm({ ...form, expected_tag: e.target.value })} placeholder="目标 MIRROR_REGISTRY_IMAGE_TAG" />
          <input value={form.previous_tag} onChange={(e) => setForm({ ...form, previous_tag: e.target.value })} placeholder="上一版本 tag" />
          <button className="primary" onClick={runPreflight}><ListChecks size={16} />预检</button>
        </div>
        <div className="chip-list">
          <span className="chip">状态 {result.summary?.status || '-'}</span>
          <span className="chip">OK {result.summary?.ok ?? 0}</span>
          <span className="chip">Warn {result.summary?.warn ?? 0}</span>
          <span className="chip">Error {result.summary?.error ?? 0}</span>
        </div>
        <div className="check-grid">{(result.checks || []).map((item: AnyRecord) => <div className="check" key={item.name}><div className="check-status"><Badge value={item.status} /></div><strong>{item.name}</strong><span className="breakable">{item.message}</span>{item.suggestion && <small className="breakable">{item.suggestion}</small>}</div>)}</div>
      </Panel>
      <Panel title="升级路径">
        <table><thead><tr><th>阶段</th><th>目标</th></tr></thead><tbody>{(guide.stages || []).map((item: AnyRecord) => <tr key={item.name}><td><Badge value={item.name} /></td><td>{item.goal}</td></tr>)}</tbody></table>
      </Panel>
      <Panel title="命令清单">
        <table><thead><tr><th>场景</th><th>命令</th></tr></thead><tbody>{Object.entries(result.commands || guide.commands || {}).map(([name, command]) => <tr key={name}><td><Badge value={name} /></td><td className="mono breakable">{String(command)}</td></tr>)}</tbody></table>
      </Panel>
      <Panel title="兼容边界">
        <pre>{JSON.stringify(guide.compatibility || [], null, 2)}</pre>
      </Panel>
    </div>
  );
}
