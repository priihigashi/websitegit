import React from "react";
import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  staticFile,
} from "remotion";

export interface ProofSlide {
  headline: string;
  fact: string;
  source: string;
  imageUrl?: string;
  startFrame: number;
  durationFrames: number;
}

export interface CaptionEntry {
  startFrame: number;
  endFrame: number;
  text: string;
}

export interface NewsReelProps {
  videoSrc: string;
  videoStartFrame?: number;
  proofSlides: ProofSlide[];
  captions: CaptionEntry[];
  language: "en" | "pt";
  totalFrames: number;
  hook?: string;             // Bold hook text shown in first 5 seconds (frames 0-150). Scroll-stopper.
  speakerName?: string;      // e.g. "Marianne Williamson"
  speakerRole?: string;      // e.g. "Author & Activist"
  topicTitle?: string;       // e.g. "REGIME CHANGE" — small strip at top of video zone
  videoOffsetY?: string;     // Vertical crop anchor, default "15%". Higher = lower crop start.
  voiceover_url?: string;    // path to /public/vo.mp3 — added by build_render_props when --voiceover flag set
}

// ─── LAYOUT CONSTANTS ──────────────────────────────────────────────────────────
const W = 1080;
const H = 1920;
const SPLIT_Y = Math.round(H * 0.58);       // 1114px — speaker zone top 58%
const PROOF_Y = SPLIT_Y + 3;                // 1117px — proof zone starts after divider
const PROOF_H = H - SPLIT_Y - 3;            // 800px
const DIVIDER_H = 3;
const PAD = 72;

// Colors — Rachadinha gold standard (matches v2_rachadinha)
const C = {
  obsidian: "#0E0D0B",
  paper: "#F2ECE0",
  accent: "#F4C430",   // canario gold — same as Rachadinha
  blood: "#8B1A1A",
  margin: "#6B6560",
};

// ─── CAPTION COMPONENT ─────────────────────────────────────────────────────────
const Caption: React.FC<{ captions: CaptionEntry[]; frame: number }> = ({ captions, frame }) => {
  const active = captions.find((c) => frame >= c.startFrame && frame < c.endFrame);
  if (!active) return null;
  return (
    <div
      style={{
        position: "absolute",
        bottom: PROOF_Y + 20,
        left: PAD,
        right: PAD,
        background: "rgba(0,0,0,0.6)",
        padding: "12px 20px",
        color: C.paper,
        fontFamily: "Inter, sans-serif",
        fontWeight: 500,
        fontSize: 40,
        lineHeight: 1.4,
        textShadow: "0 2px 8px rgba(0,0,0,0.9)",
      }}
    >
      {active.text}
    </div>
  );
};

// ─── HOOK INTRO (frames 0-150 = 5 seconds) ─────────────────────────────────────
// Large bold hook text overlaid on the video zone. Scroll-stopper.
// Only renders if hook prop is non-empty.
const HOOK_FRAMES = 150;  // 5 seconds at 30fps

const HookIntro: React.FC<{ hook: string; frame: number }> = ({ hook, frame }) => {
  const opacity = interpolate(
    frame,
    [0, 8, 140, HOOK_FRAMES],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: W,
        height: SPLIT_Y,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(11,11,12,0.72)",
        opacity,
        padding: `0 ${PAD}px`,
        zIndex: 10,
      }}
    >
      <div
        style={{
          fontFamily: "Fraunces, serif",
          fontWeight: 700,
          fontSize: 110,
          color: C.paper,
          lineHeight: 1.05,
          textAlign: "center",
          textShadow: "0 4px 24px rgba(0,0,0,0.95)",
        }}
      >
        {hook}
      </div>
    </div>
  );
};

// ─── PROOF ZONE ────────────────────────────────────────────────────────────────
const ProofZone: React.FC<{ slide: ProofSlide; frame: number; startFrame: number }> = ({
  slide,
  frame,
  startFrame,
}) => {
  const localFrame = frame - startFrame;
  const opacity = interpolate(localFrame, [0, 8], [0, 1], { extrapolateRight: "clamp" });

  return (
    <div
      style={{
        position: "absolute",
        top: PROOF_Y,
        left: 0,
        width: W,
        height: PROOF_H,
        background: C.obsidian,
        opacity,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        padding: `0 ${PAD}px`,
        gap: 16,
      }}
    >
      {slide.imageUrl && (
        <img
          src={slide.imageUrl}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: W / 2,
            height: PROOF_H,
            objectFit: "cover",
            filter: "grayscale(1) contrast(1.1)",
            opacity: 0.7,
          }}
        />
      )}
      <div
        style={{
          paddingLeft: slide.imageUrl ? W / 2 + 24 : 0,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div
          style={{
            fontFamily: "Fraunces, serif",
            fontWeight: 700,
            fontSize: 52,
            color: C.paper,
            lineHeight: 1.1,
          }}
        >
          {slide.headline}
        </div>
        <div
          style={{
            fontFamily: "Inter, sans-serif",
            fontWeight: 500,
            fontSize: 34,
            color: C.paper,
            opacity: 0.9,
          }}
        >
          {slide.fact}
        </div>
        <div
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 22,
            color: C.margin,
          }}
        >
          {slide.source}
        </div>
      </div>
    </div>
  );
};

// ─── MAIN COMPOSITION ──────────────────────────────────────────────────────────
export const NewsReel: React.FC<NewsReelProps> = ({
  videoSrc,
  videoStartFrame = 0,
  proofSlides,
  captions,
  language,
  totalFrames,
  hook,
  speakerName,
  speakerRole,
  topicTitle,
  videoOffsetY = "15%",
  voiceover_url,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const hookActive = !!hook && frame < HOOK_FRAMES;

  // Active proof slide
  const activeSlide = proofSlides.find(
    (s) => frame >= s.startFrame && frame < s.startFrame + s.durationFrames
  );

  // Video opacity: slightly dimmed during hook so hook text is legible
  const videoOpacity = hook
    ? interpolate(frame, [0, HOOK_FRAMES, HOOK_FRAMES + 8], [0.6, 0.6, 1], { extrapolateRight: "clamp" })
    : 1;

  return (
    <AbsoluteFill style={{ background: C.obsidian }}>
      {/* ── TOP ZONE: Speaker video ── */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: W,
          height: SPLIT_Y,
          overflow: "hidden",
        }}
      >
        <OffthreadVideo
          src={videoSrc}
          startFrom={videoStartFrame}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            // "50% {videoOffsetY}" — centers horizontally, anchors vertically to show face.
            // 15% from top works well for typical talking-head reels where face is in upper frame.
            objectPosition: `50% ${videoOffsetY}`,
            opacity: videoOpacity,
          }}
        />
        {/* Watermark cover — bottom strip covers IG watermark */}
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            width: W,
            height: 90,
            background: `linear-gradient(to top, ${C.obsidian}, transparent)`,
          }}
        />

        {/* Topic title strip — top of video zone. Hidden during hook to avoid overlap. */}
        {topicTitle && !hookActive && (
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: W,
              background: "rgba(11,11,12,0.82)",
              borderBottom: `3px solid ${C.accent}`,
              padding: "16px 72px",
              display: "flex",
              alignItems: "center",
              gap: 16,
            }}
          >
            <div style={{ width: 6, height: 36, background: C.accent, borderRadius: 2, flexShrink: 0 }} />
            <div
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 28,
                fontWeight: 700,
                color: C.accent,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
              }}
            >
              {topicTitle}
            </div>
          </div>
        )}

        {/* Speaker lower-third name badge */}
        {speakerName && (
          <div
            style={{
              position: "absolute",
              bottom: 96,
              left: 0,
              right: 0,
              padding: "0 72px",
            }}
          >
            <div
              style={{
                display: "inline-block",
                background: "rgba(11,11,12,0.88)",
                borderLeft: `5px solid ${C.accent}`,
                padding: "10px 20px",
              }}
            >
              <div
                style={{
                  fontFamily: "Fraunces, serif",
                  fontWeight: 700,
                  fontSize: 34,
                  color: C.paper,
                  lineHeight: 1.1,
                }}
              >
                {speakerName}
              </div>
              {speakerRole && (
                <div
                  style={{
                    fontFamily: "Inter, sans-serif",
                    fontWeight: 500,
                    fontSize: 22,
                    color: C.accent,
                    marginTop: 4,
                  }}
                >
                  {speakerRole}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Hook intro overlay — large bold text for first 5 seconds */}
        {hook && <HookIntro hook={hook} frame={frame} />}
      </div>

      {/* ── DIVIDER ── */}
      <div
        style={{
          position: "absolute",
          top: SPLIT_Y,
          left: 0,
          width: W,
          height: DIVIDER_H,
          background: C.accent,
        }}
      />

      {/* ── BOTTOM ZONE: Proof items ── */}
      {activeSlide && (
        <ProofZone slide={activeSlide} frame={frame} startFrame={activeSlide.startFrame} />
      )}

      {/* ── CAPTIONS (burned in, over video) ── */}
      <Caption captions={captions} frame={frame} />

      {/* ── HANDLE (bottom right) ── */}
      <div
        style={{
          position: "absolute",
          bottom: 24,
          right: PAD,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 22,
          color: C.margin,
        }}
      >
        @HANDLE_PLACEHOLDER
      </div>

      {/* ── VOICEOVER AUDIO (optional — injected by build_render_props --voiceover) ── */}
      {voiceover_url && (
        <Audio src={voiceover_url} startFrom={0} />
      )}
    </AbsoluteFill>
  );
};
