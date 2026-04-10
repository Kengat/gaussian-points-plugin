from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any


MAGIC = b"GASP"
VERSION = 3
KIND = "gaussian_ply_payload"
_HEADER = struct.Struct("<4sIIQQII32s")
_POINT_RECORD = struct.Struct("<" + ("f" * 62) + "i")
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_export_stem(project_name: str | None) -> str:
    stem = _INVALID_FILENAME_CHARS.sub("_", (project_name or "").strip())
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem or "Untitled Project"


def result_gasp_path(project_id: str, project_name: str | None) -> Path:
    from . import paths

    return paths.project_result_dir(project_id) / f"{safe_export_stem(project_name)}.gasp"


def read_ply_vertex_count(path: str | Path) -> int:
    path = Path(path)
    with path.open("rb") as handle:
        while True:
            line = handle.readline()
            if not line:
                raise ValueError("Unexpected EOF while reading PLY header.")
            decoded = line.decode("ascii", errors="ignore").strip()
            if decoded.startswith("element vertex"):
                return int(decoded.split()[-1])
            if decoded == "end_header":
                break
    return 0


def _infer_sh_degree(f_rest_count: int) -> int:
    if f_rest_count >= 45:
        return 3
    if f_rest_count >= 24:
        return 2
    if f_rest_count >= 9:
        return 1
    return 0


def _read_ply_header(handle) -> tuple[int, list[str]]:
    properties: list[str] = []
    vertex_count = 0
    first_line = handle.readline().decode("ascii", errors="ignore").strip()
    if first_line != "ply":
        raise ValueError("Not a PLY file.")
    format_line = handle.readline().decode("ascii", errors="ignore").strip()
    if "format binary_little_endian" not in format_line:
        raise ValueError("Only binary little-endian Gaussian PLY files are supported.")
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
    return vertex_count, properties


def _row_value(row: tuple[float, ...], prop_index: dict[str, int], name: str) -> float:
    index = prop_index.get(name)
    return float(row[index]) if index is not None else 0.0


def _build_point_buffer_from_ply(path: str | Path) -> tuple[int, bytes]:
    path = Path(path)
    chunks: list[bytes] = []
    with path.open("rb") as handle:
        vertex_count, properties = _read_ply_header(handle)
        prop_index = {name: index for index, name in enumerate(properties)}
        source_record = struct.Struct("<" + ("f" * len(properties)))
        f_rest_count = sum(1 for index in range(45) if f"f_rest_{index}" in prop_index)
        sh_degree = _infer_sh_degree(f_rest_count)

        for _ in range(vertex_count):
            payload = handle.read(source_record.size)
            if len(payload) != source_record.size:
                raise ValueError("PLY vertex buffer is truncated.")
            row = source_record.unpack(payload)
            values = [
                _row_value(row, prop_index, "x"),
                _row_value(row, prop_index, "y"),
                _row_value(row, prop_index, "z"),
                _row_value(row, prop_index, "nx"),
                _row_value(row, prop_index, "ny"),
                _row_value(row, prop_index, "nz"),
                _row_value(row, prop_index, "f_dc_0"),
                _row_value(row, prop_index, "f_dc_1"),
                _row_value(row, prop_index, "f_dc_2"),
                _row_value(row, prop_index, "scale_0"),
                _row_value(row, prop_index, "scale_1"),
                _row_value(row, prop_index, "scale_2"),
                _row_value(row, prop_index, "rot_0"),
                _row_value(row, prop_index, "rot_1"),
                _row_value(row, prop_index, "rot_2"),
                _row_value(row, prop_index, "rot_3"),
                _row_value(row, prop_index, "opacity"),
            ]
            values.extend(_row_value(row, prop_index, f"f_rest_{index}") for index in range(45))
            chunks.append(_POINT_RECORD.pack(*values, sh_degree))

    return vertex_count, b"".join(chunks)


def write_gaussian_gasp_from_ply(
    source_ply: str | Path,
    destination: str | Path,
    *,
    project: dict[str, Any] | None = None,
    extra_manifest: dict[str, Any] | None = None,
) -> Path:
    source_ply = Path(source_ply)
    destination = Path(destination)
    ply_payload = source_ply.read_bytes()
    vertex_count, point_payload = _build_point_buffer_from_ply(source_ply)
    manifest = {
        "kind": KIND,
        "version": VERSION,
        "project_id": (project or {}).get("id"),
        "project_name": (project or {}).get("name"),
        "source_format": "gaussian_ply",
        "vertex_count": vertex_count,
        "point_stride_bytes": _POINT_RECORD.size,
        "ply_sha256": hashlib.sha256(ply_payload).hexdigest(),
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    manifest_payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        handle.write(
            _HEADER.pack(
                MAGIC,
                VERSION,
                0,
                int(manifest["vertex_count"]),
                len(ply_payload),
                len(manifest_payload),
                _POINT_RECORD.size,
                hashlib.sha256(ply_payload).digest(),
            )
        )
        handle.write(manifest_payload)
        handle.write(point_payload)
        handle.write(ply_payload)
    return destination


def read_gaussian_gasp_metadata(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(_HEADER.size)
        if len(header) != _HEADER.size:
            raise ValueError("File is too small to be a Gaussian GASP.")
        magic, version, _flags, vertex_count, ply_size, manifest_size, point_stride, ply_sha = _HEADER.unpack(header)
        if magic != MAGIC or version != VERSION:
            raise ValueError("Unsupported Gaussian GASP file.")
        manifest_payload = handle.read(manifest_size)
        if len(manifest_payload) != manifest_size:
            raise ValueError("Gaussian GASP manifest is truncated.")
        manifest = json.loads(manifest_payload.decode("utf-8"))
    manifest.setdefault("vertex_count", int(vertex_count))
    manifest.setdefault("ply_size_bytes", int(ply_size))
    manifest.setdefault("point_stride_bytes", int(point_stride))
    manifest.setdefault("ply_sha256", ply_sha.hex())
    return manifest


def export_ply_from_gaussian_gasp(source_gasp: str | Path, destination: str | Path) -> Path:
    source_gasp = Path(source_gasp)
    destination = Path(destination)
    with source_gasp.open("rb") as handle:
        header = handle.read(_HEADER.size)
        if len(header) != _HEADER.size:
            raise ValueError("File is too small to be a Gaussian GASP.")
        magic, version, _flags, vertex_count, ply_size, manifest_size, point_stride, ply_sha = _HEADER.unpack(header)
        if magic != MAGIC or version != VERSION:
            raise ValueError("Unsupported Gaussian GASP file.")
        handle.seek(manifest_size, 1)
        handle.seek(int(vertex_count) * int(point_stride), 1)
        ply_payload = handle.read(ply_size)
        if len(ply_payload) != ply_size:
            raise ValueError("Gaussian GASP PLY payload is truncated.")
        if hashlib.sha256(ply_payload).digest() != ply_sha:
            raise ValueError("Gaussian GASP PLY payload checksum mismatch.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(ply_payload)
    return destination
