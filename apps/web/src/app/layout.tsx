import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import { Providers } from "@/components/providers";
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
          <header className="border-b border-slate-200 bg-white">
            <div className="nh-page-grid flex items-center justify-between py-3">
              <div>
                <h1 className="text-lg font-semibold text-teal-800">NearHome</h1>
                <p className="text-xs text-slate-500">HDB resale decision support</p>
              </div>
              {process.env.NEXT_PUBLIC_DEMO_MODE === "true" && <span className="nh-badge-demo" aria-label="Demo data mode">Demo data</span>}
            </div>
          </header>
          <main className="nh-page-grid py-6 sm:py-8">{children}</main>
        </Providers>
        <Analytics />
      </body>
    </html>
  );
}
