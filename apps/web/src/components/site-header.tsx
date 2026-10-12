"use client";

import Link from "next/link";
import { Home, Menu, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useState } from "react";

const links = [
  { href: "/#why-nearhome", label: "Why NearHome" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/how-flats-are-evaluated", label: "Resources" },
];

export function SiteHeader({ demoMode = false }: { demoMode?: boolean }) {
  const pathname = usePathname();
  const isHomepage = pathname === "/";
  // Once someone is working through a comparison, keep the header to just the logo so nothing pulls them away.
  const isWorkflow = pathname.startsWith("/session") || pathname.startsWith("/demo");
  // The methodology page keeps the header to the logo and one way into the product.
  const isMethod = pathname === "/how-flats-are-evaluated";
  const [open, setOpen] = useState(false);
  const visibleLinks = isHomepage
    ? [{ href: "/#how-it-works-demo", label: "How it works" }, { href: "/how-flats-are-evaluated", label: "How flats are evaluated" }]
    : links;

  return (
    <header className="sticky top-0 z-40 border-b border-[#d7e1dc]/80 bg-white/95 backdrop-blur">
      <div className="nh-page-grid flex min-h-[76px] items-center gap-4 py-3">
        <Link href="/" className="inline-flex items-center gap-1.5 rounded-md text-[25px] font-extrabold tracking-[-1.2px] text-[#087b72] focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-2" onClick={() => setOpen(false)}>
          <Home size={21} strokeWidth={2.1} aria-hidden="true" />
          <span>nearhome<span className="text-[#e1a74e]">.</span></span>
          {demoMode && <span className="nh-badge-demo" aria-label="Demo data mode">Demo data</span>}
        </Link>

        {!isWorkflow && !isMethod && <nav className={`ml-auto min-w-0 flex-wrap items-center justify-end gap-2 sm:gap-5 text-[12px] sm:text-[13px] text-[#344742] md:gap-7 ${isHomepage ? "flex" : "hidden md:flex"}`} aria-label="Primary navigation">
          {visibleLinks.map((link) => (
            <Link key={link.href} href={link.href} className={`py-2 font-medium transition focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-2 ${isHomepage ? "rounded-[4px] border border-[#bdcbc5] px-3 sm:px-4" : ""} ${pathname === link.href ? "text-[#087b72]" : "hover:text-[#087b72]"}`}>
              {link.label}
            </Link>
          ))}
        </nav>}

        {!isHomepage && !isWorkflow && <Link href="/#start-comparison" className={`${isMethod ? "ml-auto inline-flex" : "hidden md:inline-flex"} min-h-[40px] items-center rounded-[4px] bg-[var(--pine)] px-5 text-[13px] font-semibold text-[#fffdf7] shadow-[0_3px_0_#0d3833] transition hover:-translate-y-px hover:bg-[#0f403a]`}>
          Start a comparison
        </Link>}

        {!isHomepage && !isWorkflow && !isMethod && <button type="button" className="ml-auto nh-icon-button border-[#bdcbc5] text-[#17634e] md:hidden" aria-label={open ? "Close navigation" : "Open navigation"} aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>}
      </div>
      {open && !isWorkflow && !isMethod && (
        <nav className="border-t border-[#d7e1dc] bg-white px-4 py-3 md:hidden" aria-label="Mobile navigation">
          <div className="mx-auto flex max-w-7xl flex-col gap-1">
            {visibleLinks.map((link) => <Link key={link.href} href={link.href} onClick={() => setOpen(false)} className={`rounded-lg px-3 py-3 text-sm font-medium ${pathname === link.href ? "bg-teal-50 text-teal-800" : "text-slate-700"}`}>{link.label}</Link>)}
          </div>
        </nav>
      )}
    </header>
  );
}
