import type { Metadata } from "next";
import { Providers } from "@/components/providers";
import { SiteHeader } from "@/components/site-header";
import { DemoShell } from "@/demo/demo-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "NearHome — HDB resale decision support",
  description: "Compare 2–5 shortlisted HDB resale listings with explainable evidence and deterministic recommendations.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <DemoShell>
            <SiteHeader demoMode={process.env.NEXT_PUBLIC_DEMO_MODE === "true"} />
            <main className="min-w-0">{children}</main>
          </DemoShell>
        </Providers>
      </body>
    </html>
  );
}
