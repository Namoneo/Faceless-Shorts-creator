import React from 'react';
import { AbsoluteFill, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import type { EpisodeProps, SceneView, TimingLine } from './types';
import defaults from './fixtureDefaults.json';

const fallback = defaults as EpisodeProps;

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export const FoundationEpisode: React.FC<Partial<EpisodeProps>> = (props) => {
  const episode = props.scenes && props.scenes.length > 0 ? { ...fallback, ...props, scenes: props.scenes } : fallback;
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const time = frame / fps;
  const scene = sceneAt(episode.scenes, time);

  return (
    <AbsoluteFill style={{ background: '#1e2a3a', fontFamily: 'FoundationSans, "DejaVu Sans", sans-serif' }}>
      <style>
        {`@font-face { font-family: 'FoundationSans'; src: url('${staticFile('fonts/DejaVuSans.ttf')}') format('truetype'); font-weight: 400; }`}
      </style>
      <SubjectCard scene={scene} width={width} height={height} />
      <Caption scene={scene} time={time} width={width} height={height} />
      <div
        style={{
          position: 'absolute',
          top: height * scene.safe.top,
          left: width * scene.safe.left,
          color: '#f4efe4',
          fontSize: Math.max(18, height * 0.028),
          letterSpacing: 1.5,
        }}
      >
        {episode.label}
      </div>
    </AbsoluteFill>
  );
};

const sceneAt = (scenes: SceneView[], time: number): SceneView => {
  const found = scenes.find((scene) => time >= scene.startSec && time < scene.startSec + scene.durationSec);
  return found ?? scenes[scenes.length - 1];
};

const SubjectCard: React.FC<{ scene: SceneView; width: number; height: number }> = ({ scene, width, height }) => {
  const safeLeft = width * scene.safe.left;
  const safeTop = height * scene.safe.top;
  const safeRight = width * (1 - scene.safe.right);
  const safeBottom = height * (1 - scene.safe.bottom);
  const cardWidth = Math.min(width * 0.46, safeRight - safeLeft);
  const cardHeight = Math.min(height * 0.24, (safeBottom - safeTop) * 0.55);
  const left = clamp(scene.focal.x * width - cardWidth / 2, safeLeft, safeRight - cardWidth);
  const top = clamp(scene.focal.y * height - cardHeight / 2, safeTop, safeBottom - cardHeight);
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        width: cardWidth,
        height: cardHeight,
        background: '#f4efe4',
        color: '#1a1a1a',
        borderRadius: 18,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: Math.max(22, height * 0.034),
        padding: 16,
      }}
    >
      {scene.id}
    </div>
  );
};

const Caption: React.FC<{ scene: SceneView; time: number; width: number; height: number }> = ({
  scene,
  time,
  width,
  height,
}) => {
  const fontSize = height * scene.captionFontScale;
  return (
    <div
      style={{
        position: 'absolute',
        left: width * scene.safe.left,
        right: width * scene.safe.right,
        bottom: height * scene.safe.bottom,
        color: '#f4efe4',
        fontSize,
        lineHeight: 1.25,
        textTransform: 'none',
      }}
    >
      <CaptionBody line={scene.line} time={time} />
    </div>
  );
};

const CaptionBody: React.FC<{ line: TimingLine; time: number }> = ({ line, time }) => {
  switch (line.state) {
    case 'unaligned':
      return (
        <span data-display-text={line.text}>
          {line.text} <StateChip label="UNALIGNED" />
        </span>
      );
    case 'review':
      return (
        <span data-display-text={line.text}>
          <Words line={line} time={time} emphasize={false} /> <StateChip label="REVIEW" />
        </span>
      );
    case 'manual':
      return (
        <span data-display-text={line.text}>
          <Words line={line} time={time} emphasize />
        </span>
      );
    default: {
      const unexpected: never = line.state;
      return unexpected;
    }
  }
};

const Words: React.FC<{ line: TimingLine; time: number; emphasize: boolean }> = ({ line, time, emphasize }) => {
  const words = line.words ?? [];
  return (
    <>
      {words.map((word, index) => {
        const active = emphasize && time >= word.start && time < word.end;
        return (
          <React.Fragment key={`${word.start}-${index}`}>
            {index > 0 ? ' ' : ''}
            <span style={{ color: active ? '#f0b429' : '#f4efe4' }}>{word.w}</span>
          </React.Fragment>
        );
      })}
    </>
  );
};

const StateChip: React.FC<{ label: string }> = ({ label }) => (
  <span style={{ color: '#f0b429', fontSize: '0.62em' }}>{label}</span>
);
