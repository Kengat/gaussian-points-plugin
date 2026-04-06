# Gaussian Points Companion App

Standalone desktop companion for the SketchUp Gaussian Points plugin.

## What it does

- launches as a separate process from the SketchUp toolbar
- creates persistent photo-based splat projects
- runs a real reconstruction pipeline end-to-end
- shows job progress, logs, errors, and a lightweight 3D preview
- exports a SketchUp-friendly Gaussian PLY package and writes a `latest_export.json` handoff file

## Current backend

The default production backend is `gsplat_colmap`.

Its real training path is:

1. run COLMAP structure-from-motion on the imported photos
2. initialize gaussians from the recovered sparse points
3. optimize the scene with `gsplat` on CUDA
4. export a true gaussian PLY plus SketchUp handoff files

For local fallback and debugging, the older `builtin_visual_hull` backend still exists in the codebase, but it is no longer the default path.

## Important modules

- `app.py`: desktop UI, job controls, preview, polling
- `store.py`: persistent project/job state
- `pipeline.py`: reconstruction stages and export
- `ply.py`: Gaussian PLY writer plus preview reader
- `worker_entry.py`: background worker entry point

## SketchUp integration

- `ui/toolbar.rb` launches the companion app
- `io/companion_bridge.rb` launches the process and imports the latest export back into SketchUp
- `main.rb` registers the bridge at plugin startup

## Runtime data

The app stores projects, logs, exports, and the latest handoff manifest outside the source tree when possible. During local development it may fall back to `companion_app_data/`.
