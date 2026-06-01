import type { ReactNode } from 'react';
import {
  Activity,
  Archive,
  BarChart3,
  Boxes,
  FileKey2,
  Gauge,
  HardDrive,
  History,
  KeyRound,
  ListChecks,
  LockKeyhole,
  ServerCog,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import type { View } from './types';

export const viewMeta: Record<View, { title: string; subtitle: string; icon: ReactNode }> = {
  dashboard: { title: '概览', subtitle: '运行状态、同步心跳和关键操作。', icon: <Gauge size={18} /> },
  runs: { title: '同步任务', subtitle: '查看同步队列、执行历史和失败重试入口。', icon: <History size={18} /> },
  mirrors: { title: '镜像配置', subtitle: '维护、导入和导出上游镜像与目标 Registry。', icon: <Boxes size={18} /> },
  credentials: { title: '仓库凭据', subtitle: '加密保存源仓库和目标仓库认证信息。', icon: <KeyRound size={18} /> },
  governance: { title: '仓库治理', subtitle: '保护关键 tag、执行保留策略 dry-run 和查看恢复清单。', icon: <ShieldCheck size={18} /> },
  observability: { title: '可观测', subtitle: '同步成功率、失败聚合、趋势和告警状态。', icon: <BarChart3 size={18} /> },
  schedules: { title: '计划推送', subtitle: '管理业务镜像的定时推送策略和最近失败原因。', icon: <History size={18} /> },
  workers: { title: 'Worker', subtitle: '查看本地和远程执行节点、心跳和最近领取任务。', icon: <ServerCog size={18} /> },
  platform: { title: '平台配置', subtitle: 'Registry 目标、镜像组和多环境视图。', icon: <Archive size={18} /> },
  storage: { title: '存储管理', subtitle: '仓库 tag、删除标记和垃圾回收指引。', icon: <HardDrive size={18} /> },
  diagnostics: { title: '验证诊断', subtitle: '检查依赖、目录、数据库和同步心跳。', icon: <ListChecks size={18} /> },
  upgrade: { title: '安装升级', subtitle: '安装、升级、回滚和版本检查清单。', icon: <ListChecks size={18} /> },
  logs: { title: '日志 / 事件', subtitle: '同步日志和结构化事件。', icon: <Activity size={18} /> },
  audit: { title: '审计', subtitle: '面板和同步服务的操作记录。', icon: <ShieldCheck size={18} /> },
  access: { title: '访问控制', subtitle: '管理本地用户角色和可撤销 API Token。', icon: <LockKeyhole size={18} /> },
  security: { title: '安全', subtitle: '公网暴露边界和反向代理建议。', icon: <FileKey2 size={18} /> },
  settings: { title: '设置', subtitle: '同步间隔、并发、重试、通知和数据库配置。', icon: <Settings size={18} /> },
};

export const navGroups: Array<{ label: string; views: View[] }> = [
  { label: '总览', views: ['dashboard', 'runs', 'mirrors'] },
  { label: '发布', views: ['credentials', 'governance', 'schedules', 'workers'] },
  { label: '平台', views: ['platform', 'storage', 'diagnostics', 'upgrade'] },
  { label: '运维', views: ['observability', 'logs', 'audit', 'access', 'security', 'settings'] },
];

export const views = navGroups.flatMap((group) => group.views);
