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

export function Platform({ platform, grouped, api, reload, notify }: any) {
  const [registry, setRegistry] = useState({ id: '', name: '', url: '', copy_host: '' });
  const [group, setGroup] = useState({ id: '', name: '', project: '', environment: '', namespace: '', registry: 'local' });
  return (
    <div className="stack">
      <Panel title="Registry 目标">
        <div className="chip-list">{(platform.registries || []).map((item: AnyRecord) => <span className="chip" key={item.id}>{item.id} · {item.url}</span>)}</div>
        <div className="form-grid"><input placeholder="id" value={registry.id} onChange={(e) => setRegistry({ ...registry, id: e.target.value })} /><input placeholder="name" value={registry.name} onChange={(e) => setRegistry({ ...registry, name: e.target.value })} /><input placeholder="url" value={registry.url} onChange={(e) => setRegistry({ ...registry, url: e.target.value })} /><input placeholder="copy_host" value={registry.copy_host} onChange={(e) => setRegistry({ ...registry, copy_host: e.target.value })} /><button onClick={() => api('POST', '/registries', registry).then(() => { reload(); notify('Registry 已保存'); })}>保存</button></div>
      </Panel>
      <Panel title="镜像组">
        <div className="chip-list">{(platform.mirror_groups || []).map((item: AnyRecord) => <span className="chip" key={item.id}>{item.project}/{item.environment}/{item.namespace}</span>)}</div>
        <div className="form-grid"><input placeholder="id" value={group.id} onChange={(e) => setGroup({ ...group, id: e.target.value })} /><input placeholder="name" value={group.name} onChange={(e) => setGroup({ ...group, name: e.target.value })} /><input placeholder="project" value={group.project} onChange={(e) => setGroup({ ...group, project: e.target.value })} /><input placeholder="environment" value={group.environment} onChange={(e) => setGroup({ ...group, environment: e.target.value })} /><input placeholder="namespace" value={group.namespace} onChange={(e) => setGroup({ ...group, namespace: e.target.value })} /><button onClick={() => api('POST', '/mirror-groups', group).then(() => { reload(); notify('镜像组已保存'); })}>保存</button></div>
      </Panel>
      <Panel title="分组视图"><pre>{JSON.stringify(grouped, null, 2)}</pre></Panel>
    </div>
  );
}
