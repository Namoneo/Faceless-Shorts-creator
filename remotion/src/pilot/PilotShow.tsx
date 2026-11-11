import React from 'react';
import { AbsoluteFill, CalculateMetadataFunction, Easing, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';

export type PilotPlate = {
  id: string;
  file: string;
  focal: { x: number; y: number };
  evening: boolean;
};

export type PilotCue = { text: string; start: number; end: number };

export type PilotScene = {
  id: string;
  role: string;
  startSec: number;
  durationSec: number;
  plates: PilotPlate[];
  labels: string[];
  round: number | null;
  cues: PilotCue[];
  countdown: { start: number; end: number } | null;
  countdownBeats?: { digit: string; start: number; end: number }[] | null;
  fact: { line: string; sub: string | null; still: string } | null;
  titles: string[];
};

export type PilotProps = {
  width: number;
  height: number;
  durationInFrames: number;
  placeholder: boolean;
  title: string | null;
  ending: string | null;
  library: PilotPlate[];
  scenes: PilotScene[];
  kenBurns?: 'standard' | 'subtle';
};

export const calculatePilotMetadata: CalculateMetadataFunction<PilotProps> = ({ props }) => {
  return {
    durationInFrames: Math.max(1, props.durationInFrames),
    fps: 30,
    width: props.width,
    height: props.height,
  };
};

export const PilotShow: React.FC<PilotProps> = (props) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const time = frame / fps;
  const portrait = height > width;
  const subtle = props.kenBurns === 'subtle';
  const scene = sceneAt(props.scenes, time);
  const local = Math.max(0, time - scene.startSec);
  const sceneFrame = Math.max(0, frame - Math.round(scene.startSec * fps));
  const sceneFrames = Math.max(1, Math.round(scene.durationSec * fps));

  return (
    <AbsoluteFill style={{ background: '#140e0c', fontFamily: 'PilotSans, "DejaVu Sans", sans-serif', overflow: 'hidden' }}>
      <style>{`@font-face { font-family: 'PilotSans'; src: url('${staticFile('fonts/DejaVuSans.ttf')}') format('truetype'); font-weight: 400; }`}</style>
      <ScenePicture
        scene={scene}
        library={props.library}
        local={local}
        sceneFrame={sceneFrame}
        sceneFrames={sceneFrames}
        width={width}
        height={height}
        portrait={portrait}
        subtle={subtle}
      />
      {portrait && props.title ? <ShowTitle text={props.title} width={width} height={height} /> : null}
      {portrait && props.ending && localEnd(scene, time, props) ? (
        <EndCard text={props.ending} width={width} height={height} />
      ) : null}
      <Caption cues={scene.cues} local={local} portrait={portrait} width={width} height={height} />
      {props.placeholder ? <PlaceholderMark width={width} /> : null}
    </AbsoluteFill>
  );
};

const localEnd = (scene: PilotScene, time: number, props: PilotProps) => {
  const last = props.scenes[props.scenes.length - 1];
  if (!last || scene.id !== last.id) return false;
  const end = last.startSec + last.durationSec;
  return time >= end - 3;
};

const sceneAt = (scenes: PilotScene[], time: number): PilotScene => {
  const found = scenes.find((scene) => time >= scene.startSec && time < scene.startSec + scene.durationSec - 1e-4);
  return found ?? scenes[scenes.length - 1];
};

const ScenePicture: React.FC<{
  scene: PilotScene;
  library: PilotPlate[];
  local: number;
  sceneFrame: number;
  sceneFrames: number;
  width: number;
  height: number;
  portrait: boolean;
  subtle: boolean;
}> = ({ scene, library, local, sceneFrame, sceneFrames, width, height, portrait, subtle }) => {
  if (scene.role === 'choice' || scene.role === 'countdown') {
    return (
      <Choice
        scene={scene}
        local={local}
        sceneFrame={sceneFrame}
        sceneFrames={sceneFrames}
        width={width}
        height={height}
        portrait={portrait}
        subtle={subtle}
      />
    );
  }
  if (scene.role === 'reveal' && scene.fact) {
    const plate = library.find((item) => item.id === scene.fact?.still) ?? scene.plates[0];
    return <Fact plate={plate} fact={scene.fact} sceneFrame={sceneFrame} width={width} height={height} portrait={portrait} subtle={subtle} />;
  }
  if (scene.role === 'cta' || scene.role === 'ending') {
    return <Montage library={library} sceneFrame={sceneFrame} titles={scene.titles} width={width} height={height} subscribe subtle={subtle} />;
  }
  return <Montage library={library} sceneFrame={sceneFrame} titles={scene.titles} width={width} height={height} subscribe={false} subtle={subtle} />;
};

const Choice: React.FC<{
  scene: PilotScene;
  local: number;
  sceneFrame: number;
  sceneFrames: number;
  width: number;
  height: number;
  portrait: boolean;
  subtle: boolean;
}> = ({ scene, local, sceneFrame, sceneFrames, width, height, portrait, subtle }) => {
  const count = countAt(scene, local);
  return (
    <AbsoluteFill>
      <Half plate={scene.plates[0]} label={scene.labels[0] ?? ''} index={0} sceneFrame={sceneFrame} sceneFrames={sceneFrames} portrait={portrait} side="a" subtle={subtle} />
      <Half plate={scene.plates[1]} label={scene.labels[1] ?? ''} index={1} sceneFrame={sceneFrame} sceneFrames={sceneFrames} portrait={portrait} side="b" subtle={subtle} />
      {count ? (
        <Countdown digit={count.digit} progress={count.progress} portrait={portrait} sceneFrame={sceneFrame} />
      ) : scene.role === 'choice' ? (
        <Badge text="YOKI" portrait={portrait} sceneFrame={sceneFrame} />
      ) : null}
      {scene.round ? <RoundMark round={scene.round} portrait={portrait} /> : null}
    </AbsoluteFill>
  );
};

const countAt = (scene: PilotScene, local: number): { digit: string; progress: number } | null => {
  const beats = scene.countdownBeats;
  if (beats && beats.length > 0) {
    const beat = beats.find((item) => local >= item.start && local < item.end);
    if (!beat) return null;
    const span = Math.max(0.05, beat.end - beat.start);
    return { digit: beat.digit, progress: Math.min(0.999, (local - beat.start) / span) };
  }
  const window = scene.countdown;
  if (!window || local < window.start || local >= window.end) return null;
  const span = Math.max(0.2, window.end - window.start);
  const into = (local - window.start) / span;
  const step = Math.min(4, Math.floor(into * 5));
  const digits = ['5', '4', '3', '2', '1'];
  const progress = into * 5 - step;
  return { digit: digits[step], progress };
};

const Half: React.FC<{
  plate: PilotPlate | undefined;
  label: string;
  index: number;
  sceneFrame: number;
  sceneFrames: number;
  portrait: boolean;
  side: 'a' | 'b';
  subtle: boolean;
}> = ({ plate, label, index, sceneFrame, sceneFrames, portrait, side, subtle }) => {
  if (!plate) return null;
  const ken = kenBurns(sceneFrame, sceneFrames, index, subtle);
  const style: React.CSSProperties = portrait
    ? { position: 'absolute', left: 0, width: '100%', top: side === 'a' ? 0 : '50%', height: '50%', overflow: 'hidden' }
    : { position: 'absolute', top: 0, height: '100%', left: side === 'a' ? 0 : '50%', width: '50%', overflow: 'hidden' };
  return (
    <div style={style}>
      <Still plate={plate} ken={ken} />
      <div
        style={{
          position: 'absolute',
          left: 36,
          right: 36,
          bottom: portrait ? undefined : 170,
          top: portrait ? (side === 'b' ? 168 : undefined) : undefined,
          ...(portrait && side === 'a' ? { bottom: 168 } : {}),
          textAlign: 'center',
          color: '#fffaf3',
          fontSize: portrait ? 64 : 78,
          letterSpacing: 3,
          textShadow: '0 4px 0 rgba(0,0,0,0.85), 0 10px 24px rgba(0,0,0,0.65)',
          WebkitTextStroke: '3px rgba(0,0,0,0.55)',
          paintOrder: 'stroke fill',
        }}
      >
        {label}
      </div>
    </div>
  );
};

const Still: React.FC<{
  plate: PilotPlate;
  ken: { scale: number; x: number; y: number; frame: number };
  blur?: boolean;
}> = ({ plate, ken, blur }) => {
  const grade = plate.evening ? 'brightness(0.58) saturate(1.4) sepia(0.48) contrast(1.08)' : '';
  const soft = blur ? 'blur(8px)' : '';
  const filter = [grade, soft].filter(Boolean).join(' ') || undefined;
  return (
    <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
      <Img
        src={staticFile(plate.file)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          objectPosition: `${plate.focal.x * 100}% ${plate.focal.y * 100}%`,
          transform: `scale(${ken.scale}) translate(${ken.x}%, ${ken.y}%)`,
          filter,
        }}
      />
      {plate.evening ? <EveningGlow frame={ken.frame} /> : null}
    </div>
  );
};

const EveningGlow: React.FC<{ frame: number }> = ({ frame }) => {
  const pulse = 1 + 0.06 * Math.sin(frame / 7);
  const lamp = (left: string, top: string, size: number): React.CSSProperties => ({
    position: 'absolute',
    left,
    top,
    width: size,
    height: size * 0.72,
    transform: `scale(${pulse})`,
    background: 'radial-gradient(circle, rgba(255,186,92,0.72) 0%, rgba(255,140,40,0.28) 42%, rgba(255,120,20,0) 70%)',
    mixBlendMode: 'screen',
    pointerEvents: 'none',
  });
  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(180deg, rgba(18,8,16,0.18), rgba(24,10,4,0.42))' }} />
      <div style={lamp('18%', '38%', 280)} />
      <div style={lamp('58%', '46%', 220)} />
      <div style={lamp('40%', '62%', 160)} />
    </div>
  );
};

const kenBurns = (frame: number, frames: number, index: number, subtle: boolean) => {
  const t = interpolate(frame, [0, frames], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const dir = index % 2 === 0 ? 1 : -1;
  const travel = subtle ? 0.45 : 1.6;
  const lift = subtle ? 0.3 : 1.2;
  return {
    scale: interpolate(t, [0, 1], subtle ? [1.012, 1.035] : [1.06, 1.14]),
    x: dir * interpolate(t, [0, 1], [-travel, travel]),
    y: interpolate(t, [0, 1], [index % 2 === 0 ? lift : -lift, index % 2 === 0 ? -lift : lift]),
    frame,
  };
};

const Badge: React.FC<{ text: string; portrait: boolean; sceneFrame: number }> = ({ text, portrait, sceneFrame }) => {
  const scale = interpolate(sceneFrame, [0, 12], [0.7, 1], { extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic) });
  const size = portrait ? 132 : 148;
  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        width: size,
        height: size,
        marginLeft: -size / 2,
        marginTop: -size / 2,
        borderRadius: size,
        background: '#f6efe4',
        color: '#1b120c',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: portrait ? 32 : 36,
        letterSpacing: 1,
        transform: `scale(${scale})`,
        boxShadow: '0 10px 30px rgba(0,0,0,0.35)',
      }}
    >
      {text}
    </div>
  );
};

const RoundMark: React.FC<{ round: number; portrait: boolean }> = ({ round, portrait }) => (
  <div
    style={{
      position: 'absolute',
      top: portrait ? 118 : 36,
      left: 36,
      color: '#fffaf3',
      background: 'rgba(12,8,6,0.55)',
      borderRadius: 999,
      padding: '8px 18px',
      fontSize: portrait ? 28 : 32,
      letterSpacing: 1,
    }}
  >
    {round}/7
  </div>
);

const Countdown: React.FC<{ digit: string; progress: number; portrait: boolean; sceneFrame: number }> = ({
  digit,
  progress,
  portrait,
  sceneFrame,
}) => {
  const size = portrait ? 280 : 320;
  const radius = size * 0.38;
  const circ = 2 * Math.PI * radius;
  const pop = 1 + 0.08 * Math.sin(Math.min(progress, 1) * Math.PI);
  const tick = interpolate(sceneFrame % 15, [0, 8, 15], [0, 1, 0]);
  return (
    <div style={{ position: 'absolute', left: '50%', top: '50%', width: size, height: size, marginLeft: -size / 2, marginTop: -size / 2 }}>
      <svg width={size} height={size} style={{ position: 'absolute', inset: 0 }}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="rgba(12,8,6,0.45)" stroke="rgba(255,250,243,0.35)" strokeWidth={10} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#f6efe4"
          strokeWidth={12}
          strokeDasharray={circ}
          strokeDashoffset={circ * progress}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fffaf3',
          fontSize: portrait ? 140 : 160,
          transform: `scale(${pop})`,
          textShadow: '0 6px 0 rgba(0,0,0,0.45)',
        }}
      >
        {digit}
      </div>
      <div
        style={{
          position: 'absolute',
          right: 18,
          top: 18,
          width: 16,
          height: 16,
          borderRadius: 16,
          background: '#f6efe4',
          opacity: 0.35 + tick * 0.65,
        }}
      />
    </div>
  );
};

const Fact: React.FC<{
  plate: PilotPlate | undefined;
  fact: { line: string; sub: string | null };
  sceneFrame: number;
  width: number;
  height: number;
  portrait: boolean;
  subtle: boolean;
}> = ({ plate, fact, sceneFrame, width, height, portrait, subtle }) => {
  const rise = interpolate(sceneFrame, [0, 16], [24, 0], { extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic) });
  const opacity = interpolate(sceneFrame, [0, 12], [0, 1], { extrapolateRight: 'clamp' });
  return (
    <AbsoluteFill>
      {plate ? (
        <div style={{ position: 'absolute', inset: -40 }}>
          <Still plate={plate} ken={{ scale: subtle ? 1.03 : 1.12, x: 0, y: 0, frame: sceneFrame }} blur />
          <div style={{ position: 'absolute', inset: 0, background: 'rgba(10,6,4,0.55)' }} />
        </div>
      ) : null}
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', transform: `translateY(${rise}px)`, opacity }}>
        <div style={{ background: '#f6efe4', color: '#1b120c', borderRadius: 999, padding: portrait ? '10px 28px' : '12px 32px', fontSize: portrait ? 36 : 42, letterSpacing: 6 }}>
          FAKT
        </div>
        <div style={{ marginTop: 28, color: '#fffaf3', fontSize: portrait ? 84 : 96, textAlign: 'center', textShadow: '0 8px 24px rgba(0,0,0,0.55)', maxWidth: width * 0.8 }}>
          {fact.line}
        </div>
        {fact.sub ? (
          <div style={{ marginTop: 12, color: '#f6efe4', fontSize: portrait ? 48 : 54, textShadow: '0 6px 18px rgba(0,0,0,0.5)' }}>{fact.sub}</div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

const Montage: React.FC<{
  library: PilotPlate[];
  sceneFrame: number;
  titles: string[];
  width: number;
  height: number;
  subscribe: boolean;
  subtle: boolean;
}> = ({ library, sceneFrame, titles, width, height, subscribe, subtle }) => {
  const plates = library.length > 0 ? library : [];
  const hold = 75;
  const index = plates.length ? Math.floor(sceneFrame / hold) % plates.length : 0;
  const next = plates.length ? (index + 1) % plates.length : 0;
  const fade = plates.length ? (sceneFrame % hold) / hold : 0;
  return (
    <AbsoluteFill>
      {plates[index] ? <Still plate={plates[index]} ken={kenBurns(sceneFrame, 300, index, subtle)} blur /> : null}
      {plates[next] ? (
        <div style={{ position: 'absolute', inset: 0, opacity: fade > 0.72 ? (fade - 0.72) / 0.28 : 0 }}>
          <Still plate={plates[next]} ken={kenBurns(sceneFrame, 300, next, subtle)} blur />
        </div>
      ) : null}
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(180deg, rgba(12,8,6,0.25), rgba(12,8,6,0.55))' }} />
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 48 }}>
        <div style={{ color: '#fffaf3', fontSize: height > width ? 86 : 108, textAlign: 'center', textShadow: '0 8px 24px rgba(0,0,0,0.55)', letterSpacing: 1 }}>
          {titles[0] ?? ''}
        </div>
        {titles[1] ? (
          <div style={{ marginTop: 16, color: '#f6efe4', fontSize: height > width ? 40 : 48, letterSpacing: 4, textTransform: 'uppercase' }}>{titles[1]}</div>
        ) : null}
        {subscribe ? <Subscribe frame={sceneFrame} /> : null}
      </div>
    </AbsoluteFill>
  );
};

const Subscribe: React.FC<{ frame: number }> = ({ frame }) => {
  const scale = 1 + 0.035 * Math.sin(frame / 6);
  return (
    <div style={{ marginTop: 36, display: 'flex', alignItems: 'center', gap: 18, transform: `scale(${scale})` }}>
      <div style={{ width: 64, height: 64, borderRadius: 64, background: '#f6efe4', color: '#1b120c', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 28 }}>
        +
      </div>
      <div style={{ background: '#f6efe4', color: '#1b120c', borderRadius: 999, padding: '16px 36px', fontSize: 36, letterSpacing: 2 }}>OBUNA</div>
    </div>
  );
};

const ShowTitle: React.FC<{ text: string; width: number; height: number }> = ({ text, width, height }) => (
  <div
    style={{
      position: 'absolute',
      top: height * 0.055,
      left: width * 0.08,
      right: width * 0.08,
      textAlign: 'center',
      color: '#fffaf3',
      fontSize: 40,
      letterSpacing: 0.4,
      textShadow: '0 3px 16px rgba(0,0,0,0.7)',
    }}
  >
    {text}
  </div>
);

const EndCard: React.FC<{ text: string; width: number; height: number }> = ({ text, width, height }) => (
  <div
    style={{
      position: 'absolute',
      left: width * 0.1,
      right: width * 0.1,
      bottom: height * 0.34,
      textAlign: 'center',
      color: '#fffaf3',
      fontSize: 36,
      textShadow: '0 4px 18px rgba(0,0,0,0.75)',
    }}
  >
    {text}
  </div>
);

const PlaceholderMark: React.FC<{ width: number }> = ({ width }) => (
  <div
    style={{
      position: 'absolute',
      top: 24,
      left: 24,
      maxWidth: width * 0.7,
      color: '#1b120c',
      background: '#f6efe4',
      borderRadius: 8,
      padding: '8px 14px',
      fontSize: 22,
    }}
  >
    PLACEHOLDER NARRATION
  </div>
);

const Caption: React.FC<{ cues: PilotCue[]; local: number; portrait: boolean; width: number; height: number }> = ({
  cues,
  local,
  portrait,
  width,
  height,
}) => {
  const cue = cues.find((item) => local >= item.start && local < item.end);
  if (!cue) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: width * 0.08,
        right: width * 0.08,
        bottom: portrait ? height * 0.2 : height * 0.07,
        textAlign: 'center',
        color: '#fffaf3',
        fontSize: portrait ? 52 : 60,
        lineHeight: 1.12,
        WebkitTextStroke: '2px rgba(0,0,0,0.8)',
        paintOrder: 'stroke fill',
        textShadow: '0 3px 0 #000, 0 8px 18px rgba(0,0,0,0.55)',
      }}
    >
      <span style={{ background: 'rgba(8,6,4,0.72)', borderRadius: 16, padding: '8px 18px' }}>{cue.text}</span>
    </div>
  );
};
