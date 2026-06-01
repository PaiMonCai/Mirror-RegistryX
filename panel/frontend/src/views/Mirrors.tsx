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

function TableText({ value }: { value: string }) {
  return <span className="table-clip" title={value}>{value}</span>;
}

export function Mirrors({ mirrors, credentials, search, setSearch, api, reload, notify }: any) {
  const [form, setForm] = useState({ source: '', target: '', source_credential_id: '', target_credential_id: '' });
  const [preflightRemote, setPreflightRemote] = useState(false);
  const [preflightResult, setPreflightResult] = useState<AnyRecord | null>(null);
  const [discoveryForm, setDiscoveryForm] = useState({
    source_type: 'auto',
    target_registry: 'localhost:5000',
    mode: 'missing_only',
    trigger_sync: false,
    content: '',
  });
  const [discoveryResult, setDiscoveryResult] = useState<AnyRecord | null>(null);
  const [importText, setImportText] = useState('');
  const [replaceImport, setReplaceImport] = useState(false);
  const [importResult, setImportResult] = useState<AnyRecord | null>(null);
  async function exportMirrorConfig() {
    const bundle = await api('GET', '/mirrors/export');
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `mirror-registry-mirrors-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
    notify('镜像配置已导出');
  }
  async function importMirrorConfig() {
    let parsed: AnyRecord;
    try {
      parsed = JSON.parse(importText);
    } catch {
      notify('导入内容不是有效 JSON');
      return;
    }
    const mirrors = Array.isArray(parsed) ? parsed : parsed.mirrors;
    if (!Array.isArray(mirrors) || mirrors.length === 0) {
      notify('导入内容需要包含 mirrors 数组');
      return;
    }
    const result = await api('POST', '/mirrors/import', {
      mirrors,
      replace: replaceImport,
      registries: Array.isArray(parsed.registries) ? parsed.registries : [],
      mirror_groups: Array.isArray(parsed.mirror_groups) ? parsed.mirror_groups : [],
    });
    setImportResult(result);
    setImportText('');
    await reload();
    notify(`已导入 ${result.imported} 个镜像`);
  }
  async function discover() {
    const result = await api('POST', '/mirrors/discover', discoveryForm);
    setDiscoveryResult(result);
    notify(`发现 ${result.summary.importable} 个可导入镜像`);
  }
  async function importDiscovery() {
    const result = await api('POST', '/mirrors/discover/import', discoveryForm);
    setDiscoveryResult({ ...(discoveryResult || {}), summary: result.summary });
    await reload();
    notify(`已导入 ${result.imported} 个镜像`);
  }
  async function preflightDraft() {
    const result = await api('POST', '/mirrors/preflight', { ...form, check_remote: preflightRemote });
    setPreflightResult({ summary: { total: 1, [result.summary.status]: 1 }, items: [result] });
    notify(`预检结果: ${result.summary.status}`);
  }
  async function preflightMirror(mirror: AnyRecord) {
    const result = await api('POST', '/mirrors/preflight', { ...mirror, check_remote: preflightRemote });
    setPreflightResult({ summary: { total: 1, [result.summary.status]: 1 }, items: [result] });
    notify(`预检结果: ${result.summary.status}`);
  }
  async function preflightAll() {
    const result = await api('POST', '/mirrors/preflight/batch', { check_remote: preflightRemote });
    setPreflightResult(result);
    notify(`批量预检: ${result.summary.error} error / ${result.summary.warn} warn`);
  }
  function downloadArtifact(mirror: AnyRecord) {
    window.open(`/api/mirrors/${mirror.index}/artifact`, '_blank', 'noopener,noreferrer');
    notify('正在准备本地 registry artifact 下载（不是 Docker save/OCI tar）');
  }
  return (
    <div className="stack">
      <Panel title="添加镜像">
        <div className="form-grid">
          <input placeholder="docker.io/library/busybox:latest" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
          <input placeholder="localhost:5000/library/busybox:latest" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} />
          <select value={form.source_credential_id} onChange={(e) => setForm({ ...form, source_credential_id: e.target.value })}><option value="">源凭据自动</option>{credentials.map((c: AnyRecord) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <select value={form.target_credential_id} onChange={(e) => setForm({ ...form, target_credential_id: e.target.value })}><option value="">目标凭据自动</option>{credentials.map((c: AnyRecord) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <button className="primary" onClick={() => api('POST', '/mirrors', form).then(() => { setForm({ source: '', target: '', source_credential_id: '', target_credential_id: '' }); reload(); notify('镜像已添加'); })}>添加</button>
        </div>
      </Panel>
      <Panel title="同步预检">
        <div className="form-grid">
          <label className="checkline"><input type="checkbox" checked={preflightRemote} onChange={(e) => setPreflightRemote(e.target.checked)} />远程探测</label>
          <button onClick={preflightDraft}><ListChecks size={16} />预检当前表单</button>
          <button onClick={preflightAll}><ListChecks size={16} />批量预检</button>
        </div>
        {preflightResult && <div className="discovery-result">
          <div className="chip-list">
            <span className="chip">总数 {preflightResult.summary?.total ?? preflightResult.items?.length ?? 0}</span>
            <span className="chip">OK {preflightResult.summary?.ok ?? 0}</span>
            <span className="chip">Warn {preflightResult.summary?.warn ?? 0}</span>
            <span className="chip">Error {preflightResult.summary?.error ?? 0}</span>
            <span className="chip">{preflightRemote ? 'remote' : 'local-only'}</span>
          </div>
          <table><thead><tr><th>源镜像</th><th>目标</th><th>结果</th><th>检查项</th></tr></thead>
            <tbody>{(preflightResult.items || []).map((item: AnyRecord, index: number) => <tr key={`${item.source}-${index}`}><td>{item.source}</td><td>{item.target}</td><td><Badge value={item.summary?.status} /></td><td>{(item.checks || []).map((check: AnyRecord) => `${check.name}:${check.status}`).join(' / ')}</td></tr>)}</tbody>
          </table>
        </div>}
      </Panel>
      <Panel title="镜像发现">
        <div className="form-grid discovery-form">
          <select value={discoveryForm.source_type} onChange={(e) => setDiscoveryForm({ ...discoveryForm, source_type: e.target.value })}>
            <option value="auto">自动识别</option>
            <option value="compose">Docker Compose</option>
            <option value="kubernetes">Kubernetes YAML</option>
            <option value="text">纯文本</option>
          </select>
          <input value={discoveryForm.target_registry} onChange={(e) => setDiscoveryForm({ ...discoveryForm, target_registry: e.target.value })} placeholder="localhost:5000" />
          <select value={discoveryForm.mode} onChange={(e) => setDiscoveryForm({ ...discoveryForm, mode: e.target.value })}>
            <option value="missing_only">只导入缺失项</option>
            <option value="merge">合并导入</option>
            <option value="replace">覆盖导入</option>
          </select>
          <label className="checkline"><input type="checkbox" checked={discoveryForm.trigger_sync} onChange={(e) => setDiscoveryForm({ ...discoveryForm, trigger_sync: e.target.checked })} />导入后同步</label>
          <textarea className="discovery-textarea" value={discoveryForm.content} onChange={(e) => setDiscoveryForm({ ...discoveryForm, content: e.target.value })} placeholder="services:&#10;  web:&#10;    image: nginx:1.27" />
          <button onClick={discover}><Search size={16} />dry-run</button>
          <button className="primary" onClick={importDiscovery}>导入</button>
        </div>
        {discoveryResult && <div className="discovery-result">
          <div className="chip-list">
            <span className="chip">发现 {discoveryResult.summary?.extracted ?? 0}</span>
            <span className="chip">可导入 {discoveryResult.summary?.importable ?? 0}</span>
            <span className="chip">新增 {discoveryResult.summary?.new ?? 0}</span>
            <span className="chip">问题 {discoveryResult.problems?.length ?? discoveryResult.summary?.invalid ?? 0}</span>
          </div>
          <table><thead><tr><th>来源</th><th>源镜像</th><th>目标</th><th>状态</th></tr></thead>
            <tbody>{(discoveryResult.items || []).map((item: AnyRecord, index: number) => <tr key={`${item.location}-${index}`}><td>{item.location || item.source_type}</td><td>{item.source || item.raw}</td><td>{item.target || '-'}</td><td><Badge value={item.action} /></td></tr>)}</tbody>
          </table>
        </div>}
      </Panel>
      <Panel title="镜像导入 / 导出" action={<button onClick={exportMirrorConfig}><FileKey2 size={16} />导出 JSON</button>}>
        <div className="form-grid discovery-form">
          <label className="checkline"><input type="checkbox" checked={replaceImport} onChange={(e) => setReplaceImport(e.target.checked)} />覆盖现有镜像列表</label>
          <textarea className="discovery-textarea" value={importText} onChange={(e) => setImportText(e.target.value)} placeholder="粘贴 /api/mirrors/export 导出的 JSON，或仅包含 mirrors 数组的 JSON" />
          <button className="primary" onClick={importMirrorConfig}>导入配置</button>
        </div>
        {importResult && <div className="chip-list compact">
          <span className="chip">导入 {importResult.imported}</span>
          <span className="chip">总计 {importResult.total}</span>
          <span className="chip">{importResult.replace ? 'replace' : 'merge'}</span>
        </div>}
      </Panel>
      <Panel title="镜像列表" action={<div className="search mirror-search"><Search size={15} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索镜像、tag、环境" /></div>}>
        <div className="table-scroll mirror-table-scroll">
          <table className="mirror-table">
            <colgroup>
              <col className="mirror-col-source" />
              <col className="mirror-col-target" />
              <col className="mirror-col-credentials" />
              <col className="mirror-col-status" />
              <col className="mirror-col-actions" />
            </colgroup>
            <thead><tr><th>源</th><th>目标</th><th>凭据</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>{mirrors.map((m: AnyRecord) => {
              const sourceCredential = m.source_credential_id || `host:${hostFromImage(m.source)}`;
              const targetCredential = m.target_credential_id || `host:${hostFromImage(m.target)}`;
              return (
                <tr key={m.index}>
                  <td className="mirror-cell"><TableText value={m.source} /></td>
                  <td className="mirror-cell"><TableText value={m.target} /></td>
                  <td className="mirror-cell">
                    <div className="mirror-credentials">
                      <div className="mirror-credential">
                        <span className="mirror-credential-label">源</span>
                        <TableText value={sourceCredential} />
                      </div>
                      <div className="mirror-credential">
                        <span className="mirror-credential-label">目标</span>
                        <TableText value={targetCredential} />
                      </div>
                    </div>
                  </td>
                  <td className="mirror-cell mirror-status"><Badge value={m.synced ? 'synced' : 'pending'} /></td>
                  <td className="mirror-cell">
                    <div className="row-actions mirror-actions">
                      <button onClick={() => preflightMirror(m)}><ListChecks size={14} />预检</button>
                      <button onClick={() => api('POST', `/mirrors/${m.index}/sync`).then(() => notify('单镜像同步已入队'))}>同步</button>
                      <button onClick={() => downloadArtifact(m)}><Download size={14} />导出 artifact</button>
                      <ConfirmButton confirmText="确认重置" className="danger" onConfirm={() => api('POST', `/mirrors/${m.index}/reset`).then(reload)}>重置</ConfirmButton>
                      <ConfirmButton confirmText="确认删除" onConfirm={() => api('DELETE', `/mirrors/${m.index}`).then(reload)}><Trash2 size={14} /></ConfirmButton>
                    </div>
                  </td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
