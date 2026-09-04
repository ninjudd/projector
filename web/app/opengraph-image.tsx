import { ImageResponse } from "next/og";

export const alt = "Projector — project plans that live in Git";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
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
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <svg width="56" height="56" viewBox="0 0 32 32">
            <path d="M7 16 L29 5.5 V26.5 Z" fill="#f27c2e" />
            <circle cx="7" cy="16" r="4.5" fill="#ece8dd" />
          </svg>
          <div style={{ fontSize: 40, fontWeight: 600, letterSpacing: -1 }}>
            Projector
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
          <div
            style={{
              fontSize: 88,
              lineHeight: 1,
              letterSpacing: -3,
              fontWeight: 600,
              maxWidth: 1000,
            }}
          >
            Project plans that live in Git.
          </div>
          <div style={{ fontSize: 30, color: "#a5a094", maxWidth: 960 }}>
            One permanent home per project, one CLI for people and coding
            agents, review loops that carry a pull request to a clean head.
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
    { ...size },
  );
}
