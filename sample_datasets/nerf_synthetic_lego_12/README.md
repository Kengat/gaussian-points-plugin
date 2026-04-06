# Sample Dataset: NeRF Synthetic Lego (12 views)

This folder contains a compact 12-image subset of the `lego` scene from the NeRF Synthetic benchmark.

## Included files

- `images/`: 12 evenly spaced training views
- `transforms_train_subset.json`: camera intrinsics/extrinsics for the selected 12 views
- `lego_reference.ply`: reference point cloud artifact from the same public dataset bundle
- `source_README.txt`: upstream dataset readme

## Why this set

- small enough for quick local iteration
- known camera transforms
- widely used in NeRF / Gaussian Splatting research and open-source tooling
- good for functional tests before moving to real object photography

## Important note

The current companion app now uses a real `COLMAP + gsplat` Gaussian training path.
This bundled subset is still much smaller than the full Blender / nerf-synthetic `lego` benchmark split, so it is useful for fast iteration and sanity checks, but it should not be compared 1:1 with published full-dataset benchmark scores.

## Source

- Images and transforms: `rishitdagli/nerf-gs-datasets` on Hugging Face, `lego` scene
- Benchmark context for Blender / nerf-synthetic scenes: NerfBaselines benchmark pages
