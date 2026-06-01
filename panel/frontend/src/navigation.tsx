import type { ReactNode } from 'react';
import type { View } from './types';

export const viewMeta: Record<View, { title: string; subtitle: string; icon: ReactNode }> = {
  dashboard: { title: '概览', subtitle: '运行状态、同步心跳和关键操作。', icon: <>◈</> },
  runs: { title: '同步任务', subtitle: '查看同步队列、执行历史和失败重试入口。', icon: <>⊙</> },
  mirrors: { title: '镜像配置', subtitle: '维护、导入和导出上游镜像与目标 Registry。', icon: <>◧</> },
  credentials: { title: '仓库凭据', subtitle: '加密保存源仓库和目标仓库认证信息。', icon: <>⊞</> },
  schedules: { title: '计划推送', subtitle: '管理业务镜像的定时推送策略和最近失败原因。', icon: <>↕</> },
  governance: { title: '仓库治理', subtitle: '维护 tag 保护、保留策略、恢复演练和迁移预检。', icon: <>▦</> },
  observability: { title: '可观测', subtitle: '查看成功率、失败聚合、告警状态和同步趋势。', icon: <>⌁</> },
  workers: { title: 'Worker 状态', subtitle: '查看本地与远程 Worker 心跳，并获取 Worker 接入说明。', icon: <>⚙</> },
  install: { title: '安装升级', subtitle: '查看安装升级命令清单、升级预检和首次设置检查。', icon: <>⇧</> },
  audit: { title: '审计日志', subtitle: '查看面板和终端关键操作的审计记录。', icon: <>≣</> },
  platform: { title: '平台配置', subtitle: 'Registry 目标、镜像组和多环境视图。', icon: <>⊞</> },
  storage: { title: '存储管理', subtitle: '仓库 tag、删除标记和垃圾回收指引。', icon: <>◫</> },
  diagnostics: { title: '验证诊断', subtitle: '检查依赖、目录、数据库和同步心跳。', icon: <>⊙</> },
  logs: { title: '日志 / 事件', subtitle: '同步日志和结构化事件。', icon: <>≡</> },
  access: { title: '访问控制', subtitle: '管理本地登录用户和角色。', icon: <>⊛</> },
  security: { title: '安全', subtitle: '公网暴露边界和反向代理建议。', icon: <>◈</> },
  settings: { title: '设置', subtitle: '同步间隔、并发、重试、通知和数据库配置。', icon: <>⚙</> },
};

export const navGroups: Array<{ label: string; views: View[] }> = [
  { label: '概览', views: ['dashboard'] },
  { label: '仓库', views: ['mirrors', 'credentials', 'storage', 'governance'] },
  { label: '计划', views: ['runs', 'schedules', 'workers'] },
  { label: '运维', views: ['observability', 'install', 'diagnostics', 'logs', 'audit'] },
  { label: '设置', views: ['platform', 'settings', 'access', 'security'] },
];

export const views = navGroups.flatMap((group) => group.views);
