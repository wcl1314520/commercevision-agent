import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "商品目录 | CommerceVision",
  description: "Workspace-scoped product and SKU catalog workbench",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
