import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Human Token Tracker",
  description: "Track your input vs output tokens as a human",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
