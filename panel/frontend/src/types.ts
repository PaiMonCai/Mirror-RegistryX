export type JsonObject = Record<string, unknown>;
export type AnyRecord = Record<string, any>;

export interface Mirror {
  index: number;
  id?: string;
  source: string;
  target: string;
  enabled?: boolean;
  synced?: boolean;
  digest?: string;
  last_source_digest?: string;
  last_target_digest?: string;
  last_checked_at?: string;
  last_change_at?: string;
  last_push_at?: string;
  next_check_at?: string;
  pending_push_digest?: string;
  pending_push_target?: string;
  push_status?: string;
  check_failures?: number;
  push_failures?: number;
  next_push_at?: string;
  last_error?: string;
  mode?: string;
  check_interval_minutes?: number;
  allow_latest_push?: boolean;
  source_credential_id?: string;
  target_credential_id?: string;
  environment?: string;
  group?: string;
  project?: string;
  namespace?: string;
  registry?: string;
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
  task_type?: string;
  mirror_source?: string;
  mirror_target?: string;
  digest?: string;
  claimed_by?: string;
  claimed_at?: string;
  lease_expires_at?: string;
  priority?: number;
  attempts?: number;
  run_id?: number;
  message?: string;
  created_at?: string;
  scheduled_at?: string;
  started_at?: string;
  finished_at?: string;
}

export type View =
  | 'dashboard'
  | 'runs'
  | 'mirrors'
  | 'credentials'
  | 'storage'
  | 'logs'
  | 'settings';
