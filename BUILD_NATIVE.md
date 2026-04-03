# Native Build

Use the repo-root entrypoint:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_native.ps1 -Target all
```

Targets:

- `all`
- `gaussian`
- `pointcloud`
- `bridge`

Why this entrypoint exists:

- it normalizes the Windows `Path` / `PATH` collision that can break direct `MSBuild` runs
- it builds into `build_out/native`
- it copies fresh DLLs into the plugin runtime locations automatically

Convenience wrapper for `cmd.exe`:

```cmd
build_native.cmd all
```
