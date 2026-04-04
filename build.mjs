/**
 * build.mjs — Compile the dashboard JSX bundle with esbuild.
 *
 * Usage:
 *   node build.mjs            # one-shot production build
 *   node build.mjs --watch    # rebuild on file changes (dev)
 *
 * Produces dashboard/static/js/bundle.compiled.js which the Flask app
 * serves in place of the runtime-Babel-transpiled version.
 */
import { build, context } from 'esbuild';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const jsDir = join(__dirname, 'dashboard', 'static', 'js');

// Module order must match _JS_MODULES in app.py
const modules = [
  'utils.js',
  'hooks.js',
  'constants.js',
  'components/shared.js',
  'components/home.js',
  'components/pipeline.js',
  'stages/keywords.js',
  'stages/search.js',
  'stages/report-utils.js',
  'stages/records-panel.js',
  'stages/record-viewer.js',
  'stages/report-stage.js',
  'stages/llm.js',
  'stages/summary.js',
  'views/pipeline.js',
  'views/jobs.js',
  'app-root.js',
];

// Concatenate all modules into a single entry (same as Flask's js_bundle route)
const combined = modules
  .map(m => readFileSync(join(jsDir, m), 'utf-8'))
  .join('\n\n');

// Write a temporary entry file for esbuild
import { writeFileSync, unlinkSync } from 'fs';
const entryFile = join(jsDir, '_entry.tmp.jsx');
writeFileSync(entryFile, combined);

const opts = {
  entryPoints: [entryFile],
  outfile: join(jsDir, 'bundle.compiled.js'),
  bundle: false,         // already concatenated; we only need JSX transform
  loader: { '.jsx': 'jsx' },
  jsx: 'transform',
  jsxFactory: 'React.createElement',
  jsxFragment: 'React.Fragment',
  minify: true,
  sourcemap: false,
  target: ['es2020'],
  charset: 'utf8',
};

const isWatch = process.argv.includes('--watch');

if (isWatch) {
  const ctx = await context(opts);
  await ctx.watch();
  console.log('Watching for changes...');
} else {
  await build(opts);
  try { unlinkSync(entryFile); } catch {}
  console.log('Built bundle.compiled.js');
}
