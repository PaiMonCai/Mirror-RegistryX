import type { ReactNode } from 'react';
import type { View } from './types';

export const viewMeta: Record<View, { title: string; subtitle: string; icon: ReactNode }> = {
  dashboard: { title: '概览', subtitle: '运行状态、同步心跳和关键操作。', icon: <>◈</> },
  runs: { title: '同步任务', subtitle: '查看同步队列、执行历史和失败重试入口。', icon: <>⊙</> },
  mirrors: { title: '镜像配置', subtitle: '维护、导入和导出上游镜像与目标 Registry。', icon: <>◧</> },
  credentials: { title: '仓库凭据', subtitle: '保存源仓库和目标仓库认证信息。', icon: <>⊞</> },
  schedules: { title: '计划推送', subtitle: '管理业务镜像的定时推送策略和最近失败原因。', icon: <>↕</> },
  storage: { title: '存储管理', subtitle: '仓库 tag、删除标记和垃圾回收指引。', icon: <>◫</> },
  logs: { title: '日志 / 事件', subtitle: '同步日志和结构化事件。', icon: <>≡</> },
  settings: { title: '设置', subtitle: '同步间隔、并发、重试、通知和数据库配置。', icon: <>⚙</> },
};

export const navGroups: Array<{ label: string; views: View[] }> = [
  { label: '概览', views: ['dashboard'] },
  { label: '仓库', views: ['mirrors', 'credentials', 'storage'] },
  { label: '同步', views: ['runs', 'schedules', 'logs'] },
  { label: '设置', views: ['settings'] },
];

export const views = navGroups.flatMap((group) => group.views);
