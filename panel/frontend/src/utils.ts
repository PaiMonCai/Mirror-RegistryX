import type { AnyRecord } from './types';

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(' ');
}

export function hostFromImage(value: string) {
  const first = value.split('/')[0];
  return first.includes('.') || first.includes(':') || first === 'localhost' ? first : 'docker.io';
}

export function formatMB(value: any) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return '-';
  return `${(bytes / 1_000_000).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} MB`;
}

export function formatRate(value: any) {
  const rate = Number(value);
  if (!Number.isFinite(rate)) return '-';
  return `${(rate * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

export function formatSeconds(value: any) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return '-';
  if (seconds < 60) return `${seconds.toLocaleString(undefined, { maximumFractionDigits: 1 })} 秒`;
  if (seconds < 3600) return `${(seconds / 60).toLocaleString(undefined, { maximumFractionDigits: 1 })} 分钟`;
  return `${(seconds / 3600).toLocaleString(undefined, { maximumFractionDigits: 1 })} 小时`;
}

export function diagnosticMessage(item: AnyRecord) {
  if (item?.details?.free_bytes !== undefined && item?.details?.total_bytes !== undefined) {
    return `剩余 ${formatMB(item.details.free_bytes)} / 总计 ${formatMB(item.details.total_bytes)}`;
  }
  return item.message;
}
