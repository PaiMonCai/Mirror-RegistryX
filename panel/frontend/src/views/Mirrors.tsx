import { useState } from 'react';
import {
  Download,
  FileKey2,
  ListChecks,
  Pause,
  Play,
  PlusCircle,
  RefreshCw,
  RotateCcw,
  Search,
  SkipForward,
  Trash2,
  UploadCloud,
} from 'lucide-react';
import { Badge, Panel } from '../components/common';
import { ConfirmButton } from '../components/ConfirmButton';
import type { AnyRecord, Credential, Mirror } from '../types';
import { hostFromImage } from '../utils';

type MirrorForm = {
  source: string;
  target_registry: string;
  target_namespace: string;
  target_override: string;
  mode: string;
  check_interval_minutes: number;
  allow_latest_push: boolean;
  source_credential_id: string;
  target_credential_id: string;
};

const emptyForm: MirrorForm = {
  source: '',
  target_registry: 'localhost:5000',
  target_namespace: '',
  target_override: '',
  mode: 'auto_push',
  check_interval_minutes: 30,
  allow_latest_push: false,
  source_credential_id: '',
  target_credential_id: '',
};

function TableText({ value }: { value: string }) {
  return <span className="table-clip" title={value}>{value || '-'}</span>;
}

function CredentialSelect({
  value,
  credentials,
  placeholder,
  onChange,
}: {
  value: string;
  credentials: Credential[];
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {credentials.map((credential) => (
        <option key={credential.id} value={credential.id}>
          {credential.name} ({credential.registry_host})
        </option>
      ))}
    </select>
  );
}

function queueMessage(action: string, task: AnyRecord) {
  const id = task?.id ? ` #${task.id}` : '';
  return `${action} queued${id}`;
}

export function Mirrors({ mirrors, credentials, search, setSearch, api, reload, notify }: any) {
  const [form, setForm] = useState<MirrorForm>(emptyForm);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
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

  function updateForm(patch: Partial<MirrorForm>) {
    setForm((current) => ({ ...current, ...patch }));
  }

  function payloadFromForm() {
    return {
      ...form,
      target_override: form.target_override.trim() || null,
      target_namespace: form.target_namespace.trim() || null,
      target_registry: form.target_registry.trim() || 'localhost:5000',
      source_credential_id: form.source_credential_id || null,
      target_credential_id: form.target_credential_id || null,
    };
  }

  function editMirror(mirror: Mirror) {
    setEditingIndex(mirror.index);
    setForm({
      source: mirror.source,
      target_registry: hostFromImage(mirror.target || ''),
      target_namespace: '',
      target_override: mirror.target || '',
      mode: mirror.mode || 'auto_push',
      check_interval_minutes: mirror.check_interval_minutes || 30,
      allow_latest_push: Boolean(mirror.allow_latest_push),
      source_credential_id: mirror.source_credential_id || '',
      target_credential_id: mirror.target_credential_id || '',
    });
  }

  async function saveMirror() {
    const method = editingIndex === null ? 'POST' : 'PUT';
    const path = editingIndex === null ? '/mirrors' : `/mirrors/${editingIndex}`;
    await api(method, path, payloadFromForm());
    setForm(emptyForm);
    setEditingIndex(null);
    await reload();
    notify(editingIndex === null ? 'Mirror rule created' : 'Mirror rule updated');
  }

  async function exportMirrorConfig() {
    const bundle = await api('GET', '/mirrors/export');
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `mirror-registry-mirrors-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
    notify('Mirror config exported');
  }

  async function importMirrorConfig() {
    let parsed: AnyRecord;
    try {
      parsed = JSON.parse(importText);
    } catch {
      notify('Import content is not valid JSON');
      return;
    }
    const importedMirrors = Array.isArray(parsed) ? parsed : parsed.mirrors;
    if (!Array.isArray(importedMirrors) || importedMirrors.length === 0) {
      notify('Import JSON must include a mirrors array');
      return;
    }
    const result = await api('POST', '/mirrors/import', {
      mirrors: importedMirrors,
      replace: replaceImport,
      registries: Array.isArray(parsed.registries) ? parsed.registries : [],
      mirror_groups: Array.isArray(parsed.mirror_groups) ? parsed.mirror_groups : [],
    });
    setImportResult(result);
    setImportText('');
    await reload();
    notify(`Imported ${result.imported} mirror rules`);
  }

  async function discover() {
    const result = await api('POST', '/mirrors/discover', discoveryForm);
    setDiscoveryResult(result);
    notify(`Discovered ${result.summary.importable} importable images`);
  }

  async function importDiscovery() {
    const result = await api('POST', '/mirrors/discover/import', discoveryForm);
    setDiscoveryResult({ ...(discoveryResult || {}), summary: result.summary });
    await reload();
    notify(`Imported ${result.imported} discovered images`);
  }

  async function preflightDraft() {
    const result = await api('POST', '/mirrors/preflight', { ...payloadFromForm(), check_remote: preflightRemote });
    setPreflightResult({ summary: { total: 1, [result.summary.status]: 1 }, items: [result] });
    notify(`Preflight: ${result.summary.status}`);
  }

  async function preflightMirror(mirror: Mirror) {
    const result = await api('POST', '/mirrors/preflight', { ...mirror, check_remote: preflightRemote });
    setPreflightResult({ summary: { total: 1, [result.summary.status]: 1 }, items: [result] });
    notify(`Preflight: ${result.summary.status}`);
  }

  async function preflightAll() {
    const result = await api('POST', '/mirrors/preflight/batch', { check_remote: preflightRemote });
    setPreflightResult(result);
    notify(`Batch preflight: ${result.summary.error} error / ${result.summary.warn} warn`);
  }

  function downloadArtifact(mirror: Mirror) {
    window.open(`/api/mirrors/${mirror.index}/artifact`, '_blank', 'noopener,noreferrer');
    notify('Preparing local registry artifact');
  }

  async function triggerRule(index: number, action: string, label: string, body?: AnyRecord) {
    const result = await api('POST', `/mirrors/${index}/${action}`, body);
    await reload();
    notify(queueMessage(label, result.queue));
  }

  async function pauseOrResume(mirror: Mirror) {
    const action = mirror.enabled ? 'pause' : 'resume';
    await api('POST', `/mirrors/${mirror.index}/${action}`);
    await reload();
    notify(mirror.enabled ? 'Rule paused' : 'Rule resumed');
  }

  return (
    <div className="stack">
      <Panel title={editingIndex === null ? 'Mirror rule' : `Editing rule #${editingIndex + 1}`}>
        <div className="form-grid mirror-rule-form">
          <input
            placeholder="Source image, e.g. docker.io/library/busybox:latest"
            value={form.source}
            onChange={(event) => updateForm({ source: event.target.value })}
          />
          <input
            placeholder="Target registry, e.g. localhost:5000"
            value={form.target_registry}
            onChange={(event) => updateForm({ target_registry: event.target.value })}
          />
          <input
            placeholder="Target namespace, optional"
            value={form.target_namespace}
            onChange={(event) => updateForm({ target_namespace: event.target.value })}
          />
          <input
            placeholder="Full target override, optional"
            value={form.target_override}
            onChange={(event) => updateForm({ target_override: event.target.value })}
          />
          <select value={form.mode} onChange={(event) => updateForm({ mode: event.target.value })}>
            <option value="auto_push">Auto push</option>
            <option value="monitor_only">Monitor only</option>
          </select>
          <input
            type="number"
            min={1}
            max={1440}
            value={form.check_interval_minutes}
            onChange={(event) => updateForm({ check_interval_minutes: Number(event.target.value) || 30 })}
          />
          <CredentialSelect
            value={form.source_credential_id}
            credentials={credentials}
            placeholder="Source credential by host"
            onChange={(value) => updateForm({ source_credential_id: value })}
          />
          <CredentialSelect
            value={form.target_credential_id}
            credentials={credentials}
            placeholder="Target credential by host"
            onChange={(value) => updateForm({ target_credential_id: value })}
          />
          <label className="checkline">
            <input
              type="checkbox"
              checked={form.allow_latest_push}
              onChange={(event) => updateForm({ allow_latest_push: event.target.checked })}
            />
            Allow overwriting latest
          </label>
          <div className="row-actions">
            <button className="primary" onClick={saveMirror}><PlusCircle size={16} />{editingIndex === null ? 'Add rule' : 'Save rule'}</button>
            {editingIndex !== null && (
              <button onClick={() => { setEditingIndex(null); setForm(emptyForm); }}><RotateCcw size={16} />Cancel edit</button>
            )}
          </div>
        </div>
      </Panel>

      <Panel title="Preflight">
        <div className="form-grid">
          <label className="checkline">
            <input type="checkbox" checked={preflightRemote} onChange={(event) => setPreflightRemote(event.target.checked)} />
            Remote probe
          </label>
          <button onClick={preflightDraft}><ListChecks size={16} />Current form</button>
          <button onClick={preflightAll}><ListChecks size={16} />All rules</button>
        </div>
        {preflightResult && (
          <div className="discovery-result">
            <div className="chip-list">
              <span className="chip">Total {preflightResult.summary?.total ?? preflightResult.items?.length ?? 0}</span>
              <span className="chip">OK {preflightResult.summary?.ok ?? 0}</span>
              <span className="chip">Warn {preflightResult.summary?.warn ?? 0}</span>
              <span className="chip">Error {preflightResult.summary?.error ?? 0}</span>
              <span className="chip">{preflightRemote ? 'remote' : 'local-only'}</span>
            </div>
            <table>
              <thead><tr><th>Source</th><th>Target</th><th>Result</th><th>Checks</th></tr></thead>
              <tbody>{(preflightResult.items || []).map((item: AnyRecord, index: number) => (
                <tr key={`${item.source}-${index}`}>
                  <td>{item.source}</td>
                  <td>{item.target}</td>
                  <td><Badge value={item.summary?.status} /></td>
                  <td>{(item.checks || []).map((check: AnyRecord) => `${check.name}:${check.status}`).join(' / ')}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Discovery">
        <div className="form-grid discovery-form">
          <select value={discoveryForm.source_type} onChange={(event) => setDiscoveryForm({ ...discoveryForm, source_type: event.target.value })}>
            <option value="auto">Auto detect</option>
            <option value="compose">Docker Compose</option>
            <option value="kubernetes">Kubernetes YAML</option>
            <option value="text">Plain text</option>
          </select>
          <input value={discoveryForm.target_registry} onChange={(event) => setDiscoveryForm({ ...discoveryForm, target_registry: event.target.value })} placeholder="localhost:5000" />
          <select value={discoveryForm.mode} onChange={(event) => setDiscoveryForm({ ...discoveryForm, mode: event.target.value })}>
            <option value="missing_only">Import missing only</option>
            <option value="merge">Merge</option>
            <option value="replace">Replace</option>
          </select>
          <label className="checkline">
            <input type="checkbox" checked={discoveryForm.trigger_sync} onChange={(event) => setDiscoveryForm({ ...discoveryForm, trigger_sync: event.target.checked })} />
            Sync after import
          </label>
          <textarea className="discovery-textarea" value={discoveryForm.content} onChange={(event) => setDiscoveryForm({ ...discoveryForm, content: event.target.value })} placeholder={'services:\n  web:\n    image: nginx:1.27'} />
          <button onClick={discover}><Search size={16} />Dry run</button>
          <button className="primary" onClick={importDiscovery}><UploadCloud size={16} />Import</button>
        </div>
        {discoveryResult && (
          <div className="discovery-result">
            <div className="chip-list">
              <span className="chip">Found {discoveryResult.summary?.extracted ?? 0}</span>
              <span className="chip">Importable {discoveryResult.summary?.importable ?? 0}</span>
              <span className="chip">New {discoveryResult.summary?.new ?? 0}</span>
              <span className="chip">Issues {discoveryResult.problems?.length ?? discoveryResult.summary?.invalid ?? 0}</span>
            </div>
            <table>
              <thead><tr><th>Location</th><th>Source</th><th>Target</th><th>Status</th></tr></thead>
              <tbody>{(discoveryResult.items || []).map((item: AnyRecord, index: number) => (
                <tr key={`${item.location}-${index}`}>
                  <td>{item.location || item.source_type}</td>
                  <td>{item.source || item.raw}</td>
                  <td>{item.target || '-'}</td>
                  <td><Badge value={item.action} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Import / export" action={<button onClick={exportMirrorConfig}><FileKey2 size={16} />Export JSON</button>}>
        <div className="form-grid discovery-form">
          <label className="checkline">
            <input type="checkbox" checked={replaceImport} onChange={(event) => setReplaceImport(event.target.checked)} />
            Replace current rules
          </label>
          <textarea className="discovery-textarea" value={importText} onChange={(event) => setImportText(event.target.value)} placeholder="Paste /api/mirrors/export JSON, or JSON with a mirrors array" />
          <button className="primary" onClick={importMirrorConfig}>Import config</button>
        </div>
        {importResult && (
          <div className="chip-list compact">
            <span className="chip">Imported {importResult.imported}</span>
            <span className="chip">Total {importResult.total}</span>
            <span className="chip">{importResult.replace ? 'replace' : 'merge'}</span>
          </div>
        )}
      </Panel>

      <Panel title="Rules" action={<div className="search mirror-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search source, target, digest" /></div>}>
        <div className="table-scroll mirror-table-scroll">
          <table className="mirror-table">
            <colgroup>
              <col className="mirror-col-source" />
              <col className="mirror-col-target" />
              <col className="mirror-col-mode" />
              <col className="mirror-col-status" />
              <col className="mirror-col-actions" />
            </colgroup>
            <thead><tr><th>Source</th><th>Target</th><th>Mode</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>{mirrors.map((mirror: Mirror) => {
              const sourceCredential = mirror.source_credential_id || `host:${hostFromImage(mirror.source)}`;
              const targetCredential = mirror.target_credential_id || `host:${hostFromImage(mirror.target)}`;
              const digest = mirror.pending_push_digest || mirror.last_source_digest || mirror.digest || '';
              return (
                <tr key={mirror.index}>
                  <td className="mirror-cell">
                    <TableText value={mirror.source} />
                    <div className="mirror-meta">{sourceCredential}</div>
                  </td>
                  <td className="mirror-cell">
                    <TableText value={mirror.target} />
                    <div className="mirror-meta">{targetCredential}</div>
                  </td>
                  <td className="mirror-cell">
                    <Badge value={mirror.mode} />
                    <div className="mirror-meta">Every {mirror.check_interval_minutes || 30} min</div>
                    <div className="mirror-meta">{mirror.allow_latest_push ? 'latest allowed' : 'latest blocked'}</div>
                  </td>
                  <td className="mirror-cell mirror-status">
                    <Badge value={mirror.enabled ? mirror.push_status || (mirror.synced ? 'synced' : 'idle') : 'paused'} />
                    <div className="mirror-meta">Next check: {mirror.next_check_at || '-'}</div>
                    <div className="mirror-meta">Last check: {mirror.last_checked_at || '-'}</div>
                    <div className="mirror-meta">Digest: {digest || '-'}</div>
                    {mirror.last_error && <div className="mirror-error">{mirror.last_error}</div>}
                  </td>
                  <td className="mirror-cell">
                    <div className="row-actions mirror-actions">
                      <button title="Edit rule" onClick={() => editMirror(mirror)}><RefreshCw size={14} />Edit</button>
                      <button title="Run preflight" onClick={() => preflightMirror(mirror)}><ListChecks size={14} />Preflight</button>
                      <button title="Check source digest now" onClick={() => triggerRule(mirror.index, 'check', 'Check')}><RefreshCw size={14} />Check</button>
                      <button title="Push pending digest now" onClick={() => triggerRule(mirror.index, 'push', 'Push')}><Play size={14} />Push</button>
                      <button title={mirror.enabled ? 'Pause rule' : 'Resume rule'} onClick={() => pauseOrResume(mirror)}>
                        {mirror.enabled ? <Pause size={14} /> : <Play size={14} />}
                        {mirror.enabled ? 'Pause' : 'Resume'}
                      </button>
                      <button title="Skip pending push" onClick={() => api('POST', `/mirrors/${mirror.index}/skip-pending-push`).then(() => reload()).then(() => notify('Pending push skipped'))}><SkipForward size={14} />Skip</button>
                      <button title="Download local registry artifact" onClick={() => downloadArtifact(mirror)}><Download size={14} />Artifact</button>
                      <ConfirmButton confirmText="Reset digest state?" className="danger" onConfirm={() => api('POST', `/mirrors/${mirror.index}/reset`).then(reload)}>
                        <RotateCcw size={14} />Reset
                      </ConfirmButton>
                      <ConfirmButton confirmText="Delete this rule?" onConfirm={() => api('DELETE', `/mirrors/${mirror.index}`).then(reload)}>
                        <Trash2 size={14} />Delete
                      </ConfirmButton>
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
