import type { Metadata, Viewport } from 'next';
import './globals.css';
import SWRegister from './sw-register';

export const metadata: Metadata = {
  title: '小管家',
  description: '每日生活管家 · 日程 / 待办 / 记账 / 提醒 / 面试',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: '小管家',
  },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: 'cover',
  themeColor: '#0a0e14',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <SWRegister />
        {children}
      </body>
    </html>
  );
}
