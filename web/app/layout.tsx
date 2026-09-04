import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Instrument_Serif } from "next/font/google";
import { SITE_URL } from "@/lib/links";
import "./globals.css";

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
});

const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  style: ["normal", "italic"],
  variable: "--font-instrument-serif",
});

const title = "Projector — Project plans that live in Git";
const description =
  "Projector gives every project a permanent home under docs/projects/, one CLI for people and coding agents, and review loops that carry a pull request to a clean head.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: title,
    template: "%s · Projector",
  },
  description,
  alternates: { canonical: "/" },
  keywords: [
    "project planning",
    "git",
    "coding agents",
    "Claude Code",
    "Codex",
    "code review",
    "CLI",
  ],
  authors: [{ name: "Justin Balthrop", url: "https://github.com/ninjudd" }],
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "Projector",
    title,
    description,
  },
  twitter: {
    card: "summary_large_image",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f4ee" },
    { media: "(prefers-color-scheme: dark)", color: "#100f0c" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geist.variable} ${geistMono.variable} ${instrumentSerif.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col font-sans">{children}</body>
    </html>
  );
}
