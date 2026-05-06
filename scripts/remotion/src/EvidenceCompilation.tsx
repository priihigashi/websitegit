import React from "react";
import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  interpolate,
  spring,
  useVideoConfig,
} from "remotion";

/**
 * EvidenceCompilation — SH-104 Phase 3 video render.
 *
 * Consumes `remotion_props.json` produced by manifest_renderer.build_remotion_props().
 * Sequences segments (title → seed → context → evidence × N → verdict → sources)
 * into one continuous 9:16 1080x1920 video.
 *
 * Why this composition exists separately from NewsReel:
 *   - NewsReel binds tightly to a single source video (split-screen style).
 *   - EvidenceCompilation cycles through 6+ verified clips with attribution,
 *     timestamp overlay, and per-segment context warnings.
 *
 * Render trigger: clipmine_render.yml workflow (mode=remotion).
 *
 * Props schema (matches manifest_renderer.build_remotion_props):
 *   - segments: array of {kind, duration_sec, ...}
 *   - person_name, niche, fps, width, height
 *
 * Phase 3 note: this composition deliberately does NOT load remote video
 * sources directly — `clip.url` may be Instagram and require auth.
 * Instead, the pre-flight workflow downloads each clip locally via yt-dlp,
 * substitutes `clip_local_path` into the segment, and the composition uses
 * <OffthreadVideo src={staticFile(...)}> at render time. Phase 3 final-mile
 * adds the local-path substitution step.
 *
 * For text-only rendering (no remote media needed), the composition works
 * end-to-end on the JSON spec alone.
 */

// ── types matching scripts/research/manifest_renderer.py ────────────────────

export interface EvidenceSegment {
  kind:
    | "title_card"
    | "seed_clip"
    | "context_card"
    | "evidence_clip"
    | "verdict_card"
    | "sources_card";
  duration_sec: number;

  // title_card
  title?: string;
  subtitle?: string;
  person_name?: string;

  // seed_clip
  url?: string;
  transcript_excerpt?: string;
  role?: string;

  // context_card
  warning?: string;
  claim_type?: string;

  // evidence_clip
  platform?: string;
  uploader?: string;
  quote?: string;
  timestamp_start?: string;
  timestamp_end?: string;
  match_score?: number;
  attribution?: string;
  clip_local_path?: string; // populated by clipmine_render.yml pre-flight

  // verdict_card
  question?: string;

  // sources_card
  sources?: { url: string; platform: string }[];
}

export interface EvidenceCompilationProps {
  schema_version?: number;
  composition_id?: string;
  format?: string;
  fps?: number;
  width?: number;
  height?: number;
  duration_seconds?: number;
  person_name: string;
  niche: string;
  segment_count?: number;
  segments: EvidenceSegment[];
  constraints?: Record<string, unknown>;
}

// ── card layouts ────────────────────────────────────────────────────────────

const PALETTE = {
  bg: "#0F0F12",
  panel: "#15151A",
  ink: "#F2EFE8",
  ink_dim: "#9B988F",
  accent: "#E8C547",
  warn: "#D9534F",
};

const TitleCard: React.FC<{ title: string; subtitle?: string }> = ({
  title,
  subtitle,
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{
      backgroundColor: PALETTE.bg,
      alignItems: "center",
      justifyContent: "center",
      padding: 80,
      opacity,
    }}>
      <div style={{
        textAlign: "center",
        color: PALETTE.ink,
        fontFamily: "Inter, system-ui, sans-serif",
        fontSize: 96,
        fontWeight: 800,
        lineHeight: 1.05,
        textTransform: "uppercase",
        letterSpacing: -2,
      }}>{title}</div>
      {subtitle && (
        <div style={{
          marginTop: 32,
          color: PALETTE.accent,
          fontFamily: "Inter, system-ui, sans-serif",
          fontSize: 44,
          fontWeight: 500,
        }}>{subtitle}</div>
      )}
    </AbsoluteFill>
  );
};

const ContextCard: React.FC<{ warning: string; claim_type?: string }> = ({
  warning,
  claim_type,
}) => (
  <AbsoluteFill style={{
    backgroundColor: PALETTE.panel,
    alignItems: "center",
    justifyContent: "center",
    padding: 80,
  }}>
    <div style={{
      backgroundColor: PALETTE.warn,
      color: PALETTE.ink,
      padding: "16px 32px",
      borderRadius: 12,
      fontFamily: "Inter, system-ui, sans-serif",
      fontSize: 36,
      fontWeight: 700,
      textTransform: "uppercase",
      letterSpacing: 2,
    }}>Context Required</div>
    <div style={{
      marginTop: 48,
      color: PALETTE.ink,
      fontFamily: "Inter, system-ui, sans-serif",
      fontSize: 56,
      fontWeight: 500,
      lineHeight: 1.25,
      textAlign: "center",
      maxWidth: 900,
    }}>{warning}</div>
    {claim_type && (
      <div style={{
        marginTop: 32,
        color: PALETTE.ink_dim,
        fontFamily: "Inter, system-ui, sans-serif",
        fontSize: 28,
        fontStyle: "italic",
      }}>{claim_type}</div>
    )}
  </AbsoluteFill>
);

const EvidenceClipCard: React.FC<{ seg: EvidenceSegment }> = ({ seg }) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const slide = spring({ frame, fps, config: { damping: 200 } });
  return (
    <AbsoluteFill style={{
      backgroundColor: PALETTE.bg,
      padding: 60,
      flexDirection: "column",
      justifyContent: "space-between",
    }}>
      <div style={{ transform: `translateY(${(1 - slide) * 30}px)`, opacity: slide }}>
        <div style={{
          color: PALETTE.accent,
          fontFamily: "Inter, system-ui, sans-serif",
          fontSize: 32,
          textTransform: "uppercase",
          letterSpacing: 4,
          marginBottom: 16,
        }}>
          {seg.timestamp_start || "00:00"}–{seg.timestamp_end || "00:00"}
        </div>
        <div style={{
          color: PALETTE.ink,
          fontFamily: "Inter, system-ui, sans-serif",
          fontSize: 70,
          fontWeight: 600,
          lineHeight: 1.2,
        }}>“{seg.quote}”</div>
      </div>
      <div style={{ borderTop: `2px solid ${PALETTE.ink_dim}`, paddingTop: 24 }}>
        <div style={{
          color: PALETTE.ink_dim,
          fontFamily: "Inter, system-ui, sans-serif",
          fontSize: 28,
        }}>{seg.attribution}</div>
        {seg.match_score !== undefined && (
          <div style={{
            color: PALETTE.ink_dim,
            fontFamily: "Inter, system-ui, sans-serif",
            fontSize: 24,
            marginTop: 8,
          }}>match score: {seg.match_score.toFixed(2)}</div>
        )}
      </div>
    </AbsoluteFill>
  );
};

const SourcesCard: React.FC<{ sources?: { url: string; platform: string }[] }> = ({
  sources,
}) => (
  <AbsoluteFill style={{
    backgroundColor: PALETTE.bg,
    padding: 80,
    flexDirection: "column",
    justifyContent: "flex-start",
  }}>
    <div style={{
      color: PALETTE.accent,
      fontFamily: "Inter, system-ui, sans-serif",
      fontSize: 56,
      fontWeight: 800,
      textTransform: "uppercase",
      letterSpacing: -1,
      marginBottom: 32,
    }}>Sources</div>
    {(sources || []).slice(0, 8).map((s, i) => (
      <div key={i} style={{
        color: PALETTE.ink,
        fontFamily: "JetBrains Mono, Menlo, monospace",
        fontSize: 24,
        lineHeight: 1.6,
        wordBreak: "break-all",
      }}>
        {i + 1}. [{s.platform}] {s.url}
      </div>
    ))}
  </AbsoluteFill>
);

const VerdictCard: React.FC<{ question: string }> = ({ question }) => (
  <AbsoluteFill style={{
    backgroundColor: PALETTE.panel,
    alignItems: "center",
    justifyContent: "center",
    padding: 80,
  }}>
    <div style={{
      color: PALETTE.ink,
      fontFamily: "Inter, system-ui, sans-serif",
      fontSize: 96,
      fontWeight: 800,
      textAlign: "center",
      lineHeight: 1.1,
    }}>{question}</div>
  </AbsoluteFill>
);

// ── composition ─────────────────────────────────────────────────────────────

export const EvidenceCompilation: React.FC<EvidenceCompilationProps> = ({
  segments,
}) => {
  const { fps } = useVideoConfig();
  let cursor = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: PALETTE.bg }}>
      {segments.map((seg, idx) => {
        const dur = Math.max(1, Math.round((seg.duration_sec || 3) * fps));
        const start = cursor;
        cursor += dur;
        return (
          <Sequence
            key={idx}
            from={start}
            durationInFrames={dur}
            name={`${idx}_${seg.kind}`}
          >
            {seg.kind === "title_card" && (
              <TitleCard title={seg.title || ""} subtitle={seg.subtitle} />
            )}
            {seg.kind === "context_card" && (
              <ContextCard warning={seg.warning || ""} claim_type={seg.claim_type} />
            )}
            {seg.kind === "evidence_clip" && (
              <EvidenceClipCard seg={seg} />
            )}
            {seg.kind === "verdict_card" && (
              <VerdictCard question={seg.question || ""} />
            )}
            {seg.kind === "sources_card" && (
              <SourcesCard sources={seg.sources} />
            )}
            {seg.kind === "seed_clip" && (
              <TitleCard
                title={(seg.transcript_excerpt || "").slice(0, 120) || "Seed clip"}
                subtitle={seg.role}
              />
            )}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

// ── default props (used when no --props supplied for local preview) ─────────

export const evidenceCompilationDefaultProps: EvidenceCompilationProps = {
  schema_version: 1,
  composition_id: "EvidenceCompilation",
  format: "vertical_9_16",
  fps: 30,
  width: 1080,
  height: 1920,
  duration_seconds: 24,
  person_name: "Sample Person",
  niche: "brazil",
  segment_count: 5,
  segments: [
    {
      kind: "title_card",
      duration_sec: 2,
      title: "Sample Person",
      subtitle: "What else was said",
      person_name: "Sample Person",
    },
    {
      kind: "seed_clip",
      duration_sec: 3,
      transcript_excerpt: "Excerpt from the seed clip…",
      role: "intro_hook",
    },
    {
      kind: "evidence_clip",
      duration_sec: 5,
      quote: "Sample verified quote",
      timestamp_start: "00:42",
      timestamp_end: "00:51",
      attribution: "@sample · youtube",
      match_score: 0.83,
    },
    {
      kind: "verdict_card",
      duration_sec: 3,
      question: "What do you think?",
      person_name: "Sample Person",
    },
    {
      kind: "sources_card",
      duration_sec: 3,
      sources: [
        { url: "https://youtube.com/watch?v=XXX", platform: "youtube" },
      ],
    },
  ],
};
