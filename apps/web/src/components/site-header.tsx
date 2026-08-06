"use client";

import Link from "next/link";
import { Menu, X, House } from "lucide-react";
import { usePathname } from "next/navigation";
import { useState } from "react";

const links = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/how-flats-are-evaluated", label: "How flats are evaluated" },
];

export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b border-blue-100/80 bg-white/95 backdrop-blur">
      <div className="nh-page-grid flex min-h-20 items-center justify-between gap-4 py-3">
        <Link href="/" className="inline-flex items-center gap-2 rounded-md text-xl font-bold tracking-tight text-blue-950 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2" onClick={() => setOpen(false)}>
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-blue-50 text-blue-700" aria-hidden="true"><House size={23} strokeWidth={2.4} /></span>
          <span>NearHome</span>
        </Link>

        <nav className="hidden items-center gap-7 md:flex" aria-label="Primary navigation">
          {links.map((link) => (
            <Link key={link.href} href={link.href} className={`border-b-2 py-2 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2 ${pathname === link.href ? "border-blue-600 text-blue-700" : "border-transparent text-slate-700 hover:border-blue-200 hover:text-blue-700"}`}>
              {link.label}
            </Link>
          ))}
          <Link href="/" className="nh-primary nh-primary-blue">Start comparing</Link>
        </nav>

        <button type="button" className="nh-icon-button border-blue-200 text-blue-800 md:hidden" aria-label={open ? "Close navigation" : "Open navigation"} aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>
      {open && (
        <nav className="border-t border-blue-100 bg-white px-4 py-3 md:hidden" aria-label="Mobile navigation">
          <div className="mx-auto flex max-w-7xl flex-col gap-1">
            {links.map((link) => <Link key={link.href} href={link.href} onClick={() => setOpen(false)} className={`rounded-lg px-3 py-3 text-sm font-medium ${pathname === link.href ? "bg-blue-50 text-blue-700" : "text-slate-700"}`}>{link.label}</Link>)}
            <Link href="/" onClick={() => setOpen(false)} className="nh-primary nh-primary-blue mt-2">Start comparing</Link>
          </div>
        </nav>
      )}
    </header>
  );
}
