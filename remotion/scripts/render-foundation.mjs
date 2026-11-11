// Render cached-miss scene ranges for the offline foundation fixture.
// One bundle, then each job. No network providers.
import { bundle } from '@remotion/bundler';
import { renderMedia, selectComposition } from '@remotion/renderer';
import { mkdirSync, readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const jobsPath = process.argv[2];
if (!jobsPath) {
  console.error('usage: node scripts/render-foundation.mjs jobs.json');
  process.exit(1);
}

const payload = JSON.parse(readFileSync(jobsPath, 'utf8'));
const jobs = payload.jobs;
if (!Array.isArray(jobs) || jobs.length === 0) {
  console.error('jobs.json has no jobs');
  process.exit(1);
}

const serveUrl = await bundle({
  entryPoint: path.join(root, 'src', 'index.ts'),
  publicDir: path.join(root, '..', 'media'),
});

for (const job of jobs) {
  mkdirSync(path.dirname(job.out), { recursive: true });
  const composition = await selectComposition({
    serveUrl,
    id: job.compositionId,
    inputProps: job.inputProps,
  });
  await renderMedia({
    serveUrl,
    composition,
    inputProps: job.inputProps,
    outputLocation: job.out,
    codec: 'h264',
    crf: 23,
    x264Preset: 'veryfast',
    scale: 1,
    muted: true,
    frameRange: [job.frameStart, job.frameEnd],
    pixelFormat: 'yuv420p',
    imageFormat: 'jpeg',
    overwrite: true,
    concurrency: 2,
  });
  console.log(JSON.stringify({ rendered: job.out, compositionId: job.compositionId }));
}
