import { useEffect, useState } from 'react';
import { Badge, Panel } from '../components/common';
import type { AnyRecord } from '../types';

export function SettingsView({ settings, api, reload, notify }: any) {
  const [form, setForm] = useState<AnyRecord>({});
  const [webhookUrl, setWebhookUrl] = useState('');
  const [clearWebhookUrl, setClearWebhookUrl] = useState(false);

  useEffect(() => {
    setForm(settings || {});
    setWebhookUrl('');
    setClearWebhookUrl(false);
  }, [settings]);

  function setNumberField(key: string, value: string) {
    setForm({ ...form, [key]: value === '' ? '' : Number(value) });
  }

  function addNumber(payload: AnyRecord, key: string) {
    if (form[key] !== '' && form[key] !== undefined && form[key] !== null) {
      payload[key] = Number(form[key]);
    }
  }

  async function saveSettings() {
    const payload: AnyRecord = {};
    addNumber(payload, 'check_interval_minutes');
    addNumber(payload, 'sync_concurrency');
    addNumber(payload, 'sync_retry_count');
    if (form.database_url !== undefined) {
      payload.database_url = String(form.database_url || '');
    }
    const cleanWebhookUrl = webhookUrl.trim();
    if (clearWebhookUrl) {
      payload.clear_notify_webhook_url = true;
    } else if (cleanWebhookUrl) {
      payload.notify_webhook_url = cleanWebhookUrl;
    }
    await api('PUT', '/settings', payload);
    await reload();
    notify('设置已保存');
  }

  return (
    <Panel title="同步与飞书通知">
      <div className="form-grid">
        <input
          type="number"
          value={form.check_interval_minutes || ''}
          onChange={(event) => setNumberField('check_interval_minutes', event.target.value)}
          placeholder="同步间隔分钟"
        />
        <input
          type="number"
          value={form.sync_concurrency || ''}
          onChange={(event) => setNumberField('sync_concurrency', event.target.value)}
          placeholder="并发"
        />
        <input
          type="number"
          value={form.sync_retry_count || ''}
          onChange={(event) => setNumberField('sync_retry_count', event.target.value)}
          placeholder="重试"
        />
        <input
          value={form.database_url || ''}
          onChange={(event) => setForm({ ...form, database_url: event.target.value })}
          placeholder="DATABASE_URL"
        />
        <div className="row-actions">
          <Badge value={settings?.notify_webhook_configured ? 'configured' : 'empty'} />
          <span className="sect-desc compact">{settings?.notify_webhook_url_masked || '未配置飞书 Webhook'}</span>
        </div>
        <input
          value={webhookUrl}
          onChange={(event) => {
            setWebhookUrl(event.target.value);
            setClearWebhookUrl(false);
          }}
          placeholder="新的飞书 Webhook URL"
          aria-label="新的飞书 Webhook URL"
        />
        <label className="checkline">
          <input
            type="checkbox"
            checked={clearWebhookUrl}
            onChange={(event) => {
              setClearWebhookUrl(event.target.checked);
              if (event.target.checked) {
                setWebhookUrl('');
              }
            }}
          />
          清空飞书 Webhook
        </label>
        <button className="primary" onClick={saveSettings}>
          保存
        </button>
      </div>
    </Panel>
  );
}
