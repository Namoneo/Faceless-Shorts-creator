import React from 'react';
import { Composition } from 'remotion';
import { PilotShow, calculatePilotMetadata, type PilotProps } from './PilotShow';

const defaults: PilotProps = {
  width: 1920,
  height: 1080,
  durationInFrames: 30,
  placeholder: false,
  title: null,
  ending: null,
  library: [],
  scenes: [],
};

export const PilotRoot: React.FC = () => {
  return (
    <Composition
      id="PilotShow"
      component={PilotShow}
      durationInFrames={defaults.durationInFrames}
      fps={30}
      width={defaults.width}
      height={defaults.height}
      defaultProps={defaults}
      calculateMetadata={calculatePilotMetadata}
    />
  );
};
