import { Gauge, HardDrive, History, KeyRound, ListChecks, ScrollText, Settings, ShieldCheck, Wrench } from 'lucide-react';
import type { ReactNode } from 'react';
import type { View } from './types';

export const viewMeta: Record<View, { title: string; subtitle: string; icon: ReactNode }> = {
  dashboard: { title: '首页', subtitle: '本机镜像服务状态和常用入口。', icon: <Gauge size={15} /> },
  runs: { title: '任务', subtitle: '同步队列、执行历史和失败重试。', icon: <History size={15} /> },
  mirrors: { title: '镜像', subtitle: '添加、同步、导入和导出镜像配置。', icon: <ListChecks size={15} /> },
  credentials: { title: '凭据', subtitle: '保存 Docker Hub、GHCR 或目标仓库的账号 token。', icon: <KeyRound size={15} /> },
  storage: { title: '存储', subtitle: '查看本地仓库占用、删除标记和清理命令。', icon: <HardDrive size={15} /> },
  governance: { title: '治理', subtitle: '模板、发现、通知、窗口和批量操作。', icon: <ShieldCheck size={15} /> },
  operations: { title: '运维', subtitle: '代理心跳、服务状态、更新、重启和诊断任务。', icon: <Wrench size={15} /> },
  logs: { title: '日志', subtitle: '同步日志和事件记录。', icon: <ScrollText size={15} /> },
  settings: { title: '设置', subtitle: '同步间隔、并发、重试和飞书通知。', icon: <Settings size={15} /> },
};

export const navGroups: Array<{ label: string; views: View[] }> = [
  { label: '概览', views: ['dashboard'] },
  { label: '镜像', views: ['mirrors', 'credentials', 'storage', 'governance'] },
  { label: '同步', views: ['runs', 'operations', 'logs'] },
  { label: '设置', views: ['settings'] },
];

export const views = navGroups.flatMap((group) => group.views);
