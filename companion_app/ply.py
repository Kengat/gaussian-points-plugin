from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path


SH_C0 = 0.28209479177387814


@dataclass
class PreviewPoint:
    x: float
    y: float
    z: float
    r: float
    g: float
    b: float
    alpha: float
    scale: float


def dc_from_color(value: float) -> float:
    return (max(0.0, min(1.0, value)) - 0.5) / SH_C0


def opacity_logit(alpha: float) -> float:
    clamped = max(0.01, min(0.99, alpha))
    return math.log(clamped / (1.0 - clamped))


def opacity_sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def write_gaussian_ply(points: list[dict], destination: str | Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    header = "\n".join(
        [
            "ply",
            "format binary_little_endian 1.0",
            f"element vertex {len(points)}",
            "property float x",
            "property float y",
            "property float z",
            "property float nx",
            "property float ny",
            "property float nz",
            "property float f_dc_0",
            "property float f_dc_1",
            "property float f_dc_2",
            "property float scale_0",
            "property float scale_1",
            "property float scale_2",
            "property float rot_0",
            "property float rot_1",
            "property float rot_2",
            "property float rot_3",
            "property float opacity",
            "end_header\n",
        ]
    ).encode("ascii")
    record = struct.Struct("<" + "f" * 17)

    with destination.open("wb") as handle:
        handle.write(header)
        for point in points:
            x, y, z = point["position"]
            r, g, b = point["color"]
            alpha = point["alpha"]
            scale = point["scale"]
            handle.write(
                record.pack(
                    float(x),
                    float(y),
                    float(z),
                    0.0,
                    0.0,
                    0.0,
                    float(dc_from_color(r)),
                    float(dc_from_color(g)),
                    float(dc_from_color(b)),
                    float(scale),
                    float(scale),
                    float(scale),
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    float(opacity_logit(alpha)),
                )
            )
    return destination


def read_preview_points(path: str | Path, sample_limit: int = 8000) -> tuple[list[PreviewPoint], dict]:
    path = Path(path)
    with path.open("rb") as handle:
        properties: list[str] = []
        vertex_count = 0
        while True:
            line = handle.readline()
            if not line:
                raise ValueError("Unexpected EOF while reading PLY header.")
            decoded = line.decode("ascii", errors="ignore").strip()
            if decoded.startswith("element vertex"):
                vertex_count = int(decoded.split()[-1])
            elif decoded.startswith("property float"):
                properties.append(decoded.split()[-1])
            elif decoded == "end_header":
                break

        if vertex_count <= 0:
            return [], {"point_count": 0, "bounds": None}

        record = struct.Struct("<" + "f" * len(properties))
        stride = max(1, vertex_count // max(1, sample_limit))
        points: list[PreviewPoint] = []
        min_xyz = [float("inf")] * 3
        max_xyz = [float("-inf")] * 3

        prop_index = {name: idx for idx, name in enumerate(properties)}
        for index in range(vertex_count):
            row = record.unpack(handle.read(record.size))
            x = row[prop_index["x"]]
            y = row[prop_index["y"]]
            z = row[prop_index["z"]]
            min_xyz[0] = min(min_xyz[0], x)
            min_xyz[1] = min(min_xyz[1], y)
            min_xyz[2] = min(min_xyz[2], z)
            max_xyz[0] = max(max_xyz[0], x)
            max_xyz[1] = max(max_xyz[1], y)
            max_xyz[2] = max(max_xyz[2], z)
            if index % stride != 0:
                continue

            dc0 = row[prop_index["f_dc_0"]]
            dc1 = row[prop_index["f_dc_1"]]
            dc2 = row[prop_index["f_dc_2"]]
            opacity = row[prop_index["opacity"]]
            scale = math.exp(row[prop_index["scale_0"]]) * 20.0
            points.append(
                PreviewPoint(
                    x=x,
                    y=y,
                    z=z,
                    r=max(0.0, min(1.0, (dc0 * SH_C0) + 0.5)),
                    g=max(0.0, min(1.0, (dc1 * SH_C0) + 0.5)),
                    b=max(0.0, min(1.0, (dc2 * SH_C0) + 0.5)),
                    alpha=opacity_sigmoid(opacity),
                    scale=scale,
                )
            )

    bounds = {
        "min": min_xyz,
        "max": max_xyz,
    }
    return points, {"point_count": vertex_count, "bounds": bounds}

