import type { ReactNode } from 'react';
import type { View } from './types';

export const viewMeta: Record<View, { title: string; subtitle: string; icon: ReactNode }> = {
  dashboard: { title: '概览', subtitle: '运行状态、同步心跳和关键操作。', icon: <>◈</> },
  runs: { title: '同步任务', subtitle: '查看同步队列、执行历史和失败重试入口。', icon: <>⊙</> },
  mirrors: { title: '镜像配置', subtitle: '维护、导入和导出上游镜像与目标 Registry。', icon: <>◧</> },
  credentials: { title: '仓库凭据', subtitle: '加密保存源仓库和目标仓库认证信息。', icon: <>⊞</> },
  governance: { title: '仓库治理', subtitle: '保护关键 tag、执行保留策略 dry-run 和查看恢复清单。', icon: <>⊛</> },
  observability: { title: '可观测', subtitle: '同步成功率、失败聚合、趋势和告警状态。', icon: <>◎</> },
  schedules: { title: '计划推送', subtitle: '管理业务镜像的定时推送策略和最近失败原因。', icon: <>↕</> },
  workers: { title: 'Worker', subtitle: '查看本地和远程执行节点、心跳和最近领取任务。', icon: <>⊚</> },
  platform: { title: '平台配置', subtitle: 'Registry 目标、镜像组和多环境视图。', icon: <>⊞</> },
  storage: { title: '存储管理', subtitle: '仓库 tag、删除标记和垃圾回收指引。', icon: <>◫</> },
  diagnostics: { title: '验证诊断', subtitle: '检查依赖、目录、数据库和同步心跳。', icon: <>⊙</> },
  upgrade: { title: '安装升级', subtitle: '面向生产运维的版本检查、升级、回滚清单；日常使用可忽略。', icon: <>⟳</> },
  logs: { title: '日志 / 事件', subtitle: '同步日志和结构化事件。', icon: <>≡</> },
  audit: { title: '审计', subtitle: '面板和同步服务的操作记录。', icon: <>⊗</> },
  access: { title: '访问控制', subtitle: '管理本地登录用户和角色。', icon: <>⊛</> },
  security: { title: '安全', subtitle: '公网暴露边界和反向代理建议。', icon: <>◈</> },
  settings: { title: '设置', subtitle: '同步间隔、并发、重试、通知和数据库配置。', icon: <>⚙</> },
};

export const navGroups: Array<{ label: string; views: View[] }> = [
  { label: '监控', views: ['dashboard', 'mirrors', 'runs', 'observability'] },
  { label: '发布', views: ['credentials', 'governance', 'schedules', 'workers'] },
  { label: '平台', views: ['platform', 'storage', 'diagnostics'] },
  { label: '记录', views: ['logs', 'audit'] },
  { label: '系统', views: ['access', 'security', 'settings', 'upgrade'] },
];

export const views = navGroups.flatMap((group) => group.views);
