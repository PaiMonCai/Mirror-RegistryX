import { ShieldCheck } from 'lucide-react';
import { Badge, Metric, Panel } from '../components/common';
import type { AnyRecord } from '../types';

export function Security({ guide }: any) {
  const checks = guide.security_checks || {};
  const summary = checks.summary || {};
  return (
    <div className="stack">
      <Panel title="安全基线" action={<Badge value={summary.status || 'unknown'} />}>
        <div className="metric-grid">
          <Metric label="OK" value={summary.ok ?? 0} />
          <Metric label="Warn" value={summary.warn ?? 0} />
          <Metric label="Error" value={summary.error ?? 0} />
          <Metric label="生产模式" value={checks.production_mode ? 'yes' : 'no'} />
        </div>
        <div className="check-grid">
          {(checks.checks || []).map((check: AnyRecord) => (
            <div className="check" key={check.name}>
              <div className="check-status"><Badge value={check.status} /></div>
              <strong>{check.name}</strong>
              <span className="breakable">{check.message}</span>
              {check.suggestion && <small className="breakable">{check.suggestion}</small>}
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="公网暴露安全边界" action={<ShieldCheck size={16} />}>
        <p>{guide.public_exposure_boundary}</p>
      </Panel>
      <Panel title="Panel 登录与 Token">
        <div className="metric-grid">
          <Metric label="管理员初始化" value={guide.panel_auth?.admin_initialized ? 'yes' : 'no'} />
          <Metric label="Cookie Secure" value={guide.panel_auth?.session_cookie_secure ? 'yes' : 'no'} />
          <Metric label="SameSite" value={guide.panel_auth?.session_cookie_samesite || '-'} />
          <Metric label="TTL" value={`${guide.panel_auth?.session_ttl_seconds || 0}s`} />
        </div>
      </Panel>
      <Panel title="Nginx Basic Auth"><pre>{(guide.nginx_basic_auth || []).join('\n')}</pre></Panel>
      <Panel title="TLS / 反向代理"><ul>{(guide.tls_reverse_proxy || []).map((item: string) => <li key={item}>{item}</li>)}</ul></Panel>
      <Panel title="推荐动作"><ul>{(guide.recommended || []).map((item: string) => <li key={item}>{item}</li>)}</ul></Panel>
    </div>
  );
}
