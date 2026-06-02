import type { ReactNode } from 'react';
import type { View } from './types';

export const viewMeta: Record<View, { title: string; subtitle: string; icon: ReactNode }> = {
  dashboard: { title: '首页', subtitle: '本机镜像服务状态和常用入口。', icon: <>◈</> },
  runs: { title: '任务', subtitle: '同步队列、执行历史和失败重试。', icon: <>⊙</> },
  mirrors: { title: '镜像', subtitle: '添加、同步、导入和导出镜像配置。', icon: <>◧</> },
  credentials: { title: '凭据', subtitle: '保存 Docker Hub、GHCR 或目标仓库的账号 token。', icon: <>⊞</> },
  storage: { title: '存储', subtitle: '查看本地仓库占用、删除标记和清理命令。', icon: <>◫</> },
  logs: { title: '日志', subtitle: '同步日志和事件记录。', icon: <>≡</> },
  settings: { title: '设置', subtitle: '同步间隔、并发、重试和飞书通知。', icon: <>⚙</> },
};

export const navGroups: Array<{ label: string; views: View[] }> = [
  { label: '概览', views: ['dashboard'] },
  { label: '镜像', views: ['mirrors', 'credentials', 'storage'] },
  { label: '同步', views: ['runs', 'logs'] },
  { label: '设置', views: ['settings'] },
];

export const views = navGroups.flatMap((group) => group.views);
