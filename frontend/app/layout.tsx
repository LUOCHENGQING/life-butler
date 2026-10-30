import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '小管家 · 每日生活管家 Agent',
  description: '基于 LangGraph + DeepSeek 的全栈生活助手：日程 / 待办 / 记账 / 提醒',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
