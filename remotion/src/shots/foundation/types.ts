export type TimingState = 'unaligned' | 'review' | 'manual';

export type TimingWord = {
  w: string;
  start: number;
  end: number;
  state: 'review' | 'manual';
};

export type TimingLine = {
  sceneId: string;
  text: string;
  state: TimingState;
  words?: TimingWord[];
  stale?: boolean;
};

export type SceneView = {
  id: string;
  startSec: number;
  durationSec: number;
  focal: { x: number; y: number };
  safe: { top: number; bottom: number; left: number; right: number };
  text: string;
  captionFontScale: number;
  captionMaxLines: number;
  line: TimingLine;
};

export type EpisodeProps = {
  language: string;
  label: string;
  scenes: SceneView[];
};
