import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  staticFile,
} from "remotion";

/**
 * CarouselMotion — 1080×1350 loopable motion layer for Instagram carousel slides.
 *
 * Sibling renderer to Playwright's record_motion.js. Used when motion_renderer === "remotion"
 * (default for cover slides in Brazil/USA/OPC news carousels).
 *
 * Props:
 *   posterPng   — path under /public or absolute URL. Static fallback image shown
 *                 when no clip OR as the final frame of the loop.
 *   clipSrc     — optional MP4/WEBM clip path. If provided, plays on top of poster
 *                 with a gentle fade + slow zoom. If empty, poster gets Ken-Burns-style
 *                 zoom via Remotion interpolate (no ffmpeg required).
 *   hookText    — optional overlay string (e.g. "RACHADINHA"). Renders top-left.
 *   accentColor — hex for hook underline. Default canario gold #F4C430.
 *
 * Duration: fixed at 150 frames @ 30fps = 5 seconds (matches Playwright loop).
 */

export interface CarouselMotionProps {
  posterPng: string;
  clipSrc?: string;
  hookText?: string;
  accentColor?: string;
}

const W = 1080;
const H = 1350;

export const CarouselMotion: React.FC<CarouselMotionProps> = ({
  posterPng,
  clipSrc,
  hookText,
  accentColor = "#F4C430",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  // Slow ken-burns style zoom over full duration — always applied to the poster.
  const zoom = interpolate(frame, [0, durationInFrames], [1.0, 1.06], {
    extrapolateRight: "clamp",
  });

  // Clip fade-in (first 10 frames) and fade-out (last 10 frames) for seamless loop.
  const clipOpacity = clipSrc
    ? interpolate(
        frame,
        [0, 10, durationInFrames - 10, durationInFrames],
        [0, 1, 1, 0],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
      )
    : 0;

  return (
    <AbsoluteFill style={{ background: "#0E0D0B", overflow: "hidden" }}>
      {/* Poster (always present, zoomed) */}
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: W,
          height: H,
          transform: `scale(${zoom})`,
          transformOrigin: "50% 50%",
        }}
      >
        <Img
          src={posterPng.startsWith("http") || posterPng.startsWith("/")
            ? posterPng
            : staticFile(posterPng)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
          }}
        />
      </div>

      {/* Clip overlay (when present) */}
      {clipSrc && (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: W,
            height: H,
            opacity: clipOpacity,
          }}
        >
          <OffthreadVideo
            src={
              clipSrc.startsWith("http") || clipSrc.startsWith("/")
                ? clipSrc
                : staticFile(clipSrc)
            }
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
            }}
          />
        </div>
      )}

      {/* Optional hook overlay — top-left strip. Kept minimal so the PNG artwork reads through. */}
      {hookText && (
        <div
          style={{
            position: "absolute",
            top: 48,
            left: 48,
            right: 48,
            display: "flex",
            alignItems: "center",
            gap: 16,
            zIndex: 5,
          }}
        >
          <div style={{ width: 6, height: 44, background: accentColor, borderRadius: 2 }} />
          <div
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 28,
              fontWeight: 700,
              color: accentColor,
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              textShadow: "0 2px 12px rgba(0,0,0,0.9)",
            }}
          >
            {hookText}
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};
