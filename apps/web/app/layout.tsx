import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CommerceVision Agent",
  description: "Durable ecommerce visual workflow control plane",
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
