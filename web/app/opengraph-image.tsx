import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";

export const alt = "Projector — agents plan, build, and review. You make the calls.";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// The site sets its headline in Instrument Serif and everything else in Geist
// (see app/layout.tsx). ImageResponse cannot use next/font, so it reads the
// same faces from TTF files committed under assets/fonts.
const font = (file: string) => readFile(join(process.cwd(), "assets/fonts", file));

export default async function Image() {
  const [serif, serifItalic, sans, sansSemiBold] = await Promise.all([
    font("InstrumentSerif-Regular.ttf"),
    font("InstrumentSerif-Italic.ttf"),
    font("Geist-Regular.ttf"),
    font("Geist-SemiBold.ttf"),
  ]);

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "#100f0c",
          color: "#ece8dd",
          fontFamily: "Geist",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <svg width="52" height="52" viewBox="0 0 32 32">
            <path d="M7 16 L29 5.5 V26.5 Z" fill="#f27c2e" />
            <circle cx="7" cy="16" r="4.5" fill="#ece8dd" />
          </svg>
          <div style={{ fontSize: 40, fontWeight: 600, letterSpacing: -1 }}>
            Projector
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              fontFamily: "Instrument Serif",
              fontSize: 100,
              lineHeight: 0.98,
              letterSpacing: -2,
            }}
          >
            <div>Agents plan, build, and review.</div>
            <div style={{ fontStyle: "italic", color: "#f27c2e" }}>
              You make the calls.
            </div>
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              fontSize: 30,
              lineHeight: 1.4,
              color: "#a5a094",
            }}
          >
            <div>A framework for building in Claude Code or Codex.</div>
            <div>
              You review the plan, answer what only you can, and decide what
              merges.
            </div>
          </div>
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 26,
            color: "#8d877a",
          }}
        >
          <div>projector.bot</div>
          <div>Claude Code · Codex · MIT</div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "Instrument Serif", data: serif, weight: 400, style: "normal" },
        { name: "Instrument Serif", data: serifItalic, weight: 400, style: "italic" },
        { name: "Geist", data: sans, weight: 400, style: "normal" },
        { name: "Geist", data: sansSemiBold, weight: 600, style: "normal" },
      ],
    },
  );
}
