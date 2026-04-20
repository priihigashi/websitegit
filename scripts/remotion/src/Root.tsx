import React from "react";
import { Composition, registerRoot } from "remotion";
import { NewsReel, NewsReelProps } from "./NewsReel";
import { CarouselMotion, CarouselMotionProps } from "./CarouselMotion";

// Default props for development previews — overridden by --props in CI render
const defaultProps: NewsReelProps = {
  videoSrc: "./public/source_clip.mp4",
  videoStartFrame: 0,
  proofSlides: [
    {
      headline: "1953. CIA OVERTHREW IRAN'S DEMOCRACY.",
      fact: "Operation AJAX removed democratically elected PM Mossadegh.",
      source: "CIA declassified files, 2013",
      startFrame: 90,
      durationFrames: 150,
    },
  ],
  captions: [],
  language: "en",
  totalFrames: 900,
  speakerName: "Marianne Williamson",
  speakerRole: "Author & Activist",
  topicTitle: "REGIME CHANGE",
};

// CarouselMotion default props — overridden by --props in CI render.
// 1080x1350 matches Instagram carousel slide dimensions; 150 frames @ 30fps = 5s loop.
const carouselDefaultProps: CarouselMotionProps = {
  posterPng: "./public/poster_placeholder.png",
  clipSrc: undefined,
  hookText: undefined,
  accentColor: "#F4C430",
};

const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="NewsReelEN"
        component={NewsReel}
        durationInFrames={900}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{ ...defaultProps, language: "en" }}
      />
      <Composition
        id="NewsReelPT"
        component={NewsReel}
        durationInFrames={900}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{ ...defaultProps, language: "pt" }}
      />
      <Composition
        id="CarouselMotion"
        component={CarouselMotion}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1350}
        defaultProps={carouselDefaultProps}
      />
    </>
  );
};

registerRoot(RemotionRoot);
