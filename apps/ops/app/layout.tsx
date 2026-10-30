import type { Metadata } from "next";
import { ToastProvider } from "./components/Toast";
import "./globals.css";

export const metadata: Metadata = {
  title: "media-platform · ops",
  description: "Configure the unified Discord bot: sources, subscriptions, routes.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-neutral-950 text-neutral-100 antialiased">
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
