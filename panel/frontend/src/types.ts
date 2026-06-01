export type JsonObject = Record<string, unknown>;
export type AnyRecord = Record<string, any>;

export interface Mirror {
  index: number;
  source: string;
  target: string;
  synced?: boolean;
  digest?: string;
  source_credential_id?: string;
  target_credential_id?: string;
  environment?: string;
  group?: string;
}

export interface Credential {
  id: string;
  name: string;
  registry_host: string;
  username?: string;
  scope?: string;
  configured?: boolean;
}

export interface SyncRun {
  id: number;
  reason?: string;
  status: string;
  updated?: number;
  failed?: number;
  started_at?: string;
  finished_at?: string;
}

export interface SyncQueueTask {
  id: number;
  reason?: string;
  status: string;
  sources?: string[];
  priority?: number;
  attempts?: number;
  run_id?: number;
  message?: string;
  created_at?: string;
  scheduled_at?: string;
  started_at?: string;
  finished_at?: string;
}

export interface WorkerStatus {
  worker_id: string;
  name?: string;
  environment?: string;
  status: string;
  labels?: string[];
  capabilities?: string[];
  last_heartbeat?: string;
  latest_claim?: AnyRecord;
  message?: string;
}

export type View =
  | 'dashboard'
  | 'runs'
  | 'mirrors'
  | 'credentials'
  | 'schedules'
  | 'governance'
  | 'observability'
  | 'workers'
  | 'install'
  | 'audit'
  | 'platform'
  | 'storage'
  | 'diagnostics'
  | 'logs'
  | 'access'
  | 'security'
  | 'settings';
