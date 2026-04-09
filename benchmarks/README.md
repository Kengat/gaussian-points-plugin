# Quality Baselines

This folder stores persistent benchmark references for Gaussian Points training quality.

Main file:

- `quality_baselines.json` contains fixed canary and real-capture reference values.

Default behavior:

- `scripts/benchmark_training.py` automatically loads `benchmarks/quality_baselines.json` when it exists.
- The benchmark report includes `baseline_comparison` with metric deltas when a matching entry is found.
- Baseline gates are applied automatically unless stricter CLI gates are provided.

Example:

```powershell
.\.gstrain310\Scripts\python.exe scripts\benchmark_training.py `
  sample_datasets\nerf_synthetic_lego_12\images `
  --steps 25 `
  --preset compact `
  --strategy auto
```

To compare a one-off run against a named baseline entry:

```powershell
.\.gstrain310\Scripts\python.exe scripts\benchmark_training.py `
  sample_datasets\nerf_synthetic_lego_12\images `
  --steps 25 `
  --preset compact `
  --strategy auto `
  --baseline-key sample_datasets/nerf_synthetic_lego_12/images|compact|auto|25|640
```
