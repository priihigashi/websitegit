import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
  interpolate,
} from "remotion";

/**
 * CarouselReel — sequences carousel slides into one continuous 9:16 Instagram Reel.
 *
 * Each slide is rendered as a letterboxed 4:5 (1080×1350) frame centered in the
 * 1080×1920 output, with black bars top/bottom (same layout as build_carousel_reel.sh).
 *
 * Tier 1 in the reel-build cascade. Falls through to build_carousel_reel.sh (FFmpeg)
 * when Remotion is unavailable or render fails.
 *
 * Props:
 *   slides             — array of slide descriptors (posterPng required; rest optional)
 *   slideDurationFrames — frames per slide at 30fps. Default 150 = 5 seconds.
 */

export interface CarouselReelSlide {
  posterPng: string;
  clipSrc?: string;
  hookText?: string;
  accentColor?: string;
}

export interface CarouselReelProps {
  slides: CarouselReelSlide[];
  slideDurationFrames?: number;
}

const W = 1080;
const H = 1920;
const SLIDE_H = 1350;
const SLIDE_Y = (H - SLIDE_H) / 2; // 285px letterbox bars

const SlideFrame: React.FC<{
  slide: CarouselReelSlide;
  durationFrames: number;
}> = ({ slide, durationFrames }) => {
  const frame = useCurrentFrame();
  const color = slide.accentColor || "#F4C430";

  const zoom = interpolate(frame, [0, durationFrames], [1.0, 1.06], {
    extrapolateRight: "clamp",
  });

  const clipOpacity = slide.clipSrc
    ? interpolate(
        frame,
        [0, 10, durationFrames - 10, durationFrames],
        [0, 1, 1, 0],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
      )
    : 0;

  const posterSrc =
    slide.posterPng.startsWith("http") || slide.posterPng.startsWith("/")
      ? slide.posterPng
      : staticFile(slide.posterPng);

  return (
    <AbsoluteFill style={{ background: "#0E0D0B" }}>
      {/* Letterboxed 4:5 slide area */}
      <div
        style={{
          position: "absolute",
          top: SLIDE_Y,
          left: 0,
          width: W,
          height: SLIDE_H,
          overflow: "hidden",
        }}
      >
        {/* Poster + Ken Burns zoom */}
        <div
          style={{
            width: "100%",
            height: "100%",
            transform: `scale(${zoom})`,
            transformOrigin: "50% 50%",
          }}
        >
          <Img
            src={posterSrc}
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        </div>

        {/* Optional clip overlay */}
        {slide.clipSrc && (
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
              opacity: clipOpacity,
            }}
          >
            <OffthreadVideo
              src={
                slide.clipSrc.startsWith("http") || slide.clipSrc.startsWith("/")
                  ? slide.clipSrc
                  : staticFile(slide.clipSrc)
              }
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          </div>
        )}

        {/* Optional hook text overlay — top-left strip */}
        {slide.hookText && (
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
            <div
              style={{
                width: 6,
                height: 44,
                background: color,
                borderRadius: 2,
              }}
            />
            <div
              style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 28,
                fontWeight: 700,
                color,
                letterSpacing: "0.14em",
                textTransform: "uppercase",
                textShadow: "0 2px 12px rgba(0,0,0,0.9)",
              }}
            >
              {slide.hookText}
            </div>
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};

export const CarouselReel: React.FC<CarouselReelProps> = ({
  slides,
  slideDurationFrames = 150,
}) => (
  <AbsoluteFill>
    {slides.map((slide, i) => (
      <Sequence
        key={i}
        from={i * slideDurationFrames}
        durationInFrames={slideDurationFrames}
      >
        <SlideFrame slide={slide} durationFrames={slideDurationFrames} />
      </Sequence>
    ))}
  </AbsoluteFill>
);
