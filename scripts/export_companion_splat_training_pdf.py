from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


LANDSCAPE_A4 = (842.0, 595.0)
PAGE_WIDTH, PAGE_HEIGHT = LANDSCAPE_A4
MARGIN = 36.0
HEADER_GAP = 18.0
FOOTER_GAP = 18.0
CODE_FONT_SIZE = 7.0
CODE_LEADING = 8.2
HEADER_FONT_SIZE = 12.0
META_FONT_SIZE = 8.0
MAX_CODE_CHARS = int((PAGE_WIDTH - (MARGIN * 2.0)) / (CODE_FONT_SIZE * 0.60))
DEFAULT_OUTPUT_NAME = "companion_splat_training_code.pdf"

SOURCE_FILES = [
    "companion_app/app.py",
    "companion_app/desktop_app.py",
    "companion_app/web_desktop_app.py",
    "companion_app/worker_entry.py",
    "companion_app/pipeline.py",
    "companion_app/gsplat_pipeline.py",
    "companion_app/store.py",
    "companion_app/paths.py",
    "companion_app/quality.py",
    "companion_app/gaussian_gasp.py",
]


@dataclass
class SourceDocument:
    relative_path: str
    absolute_path: Path
    content: str

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines())


def _read_sources(repo_root: Path) -> list[SourceDocument]:
    sources: list[SourceDocument] = []
    for relative_path in SOURCE_FILES:
        absolute_path = repo_root / relative_path
        sources.append(
            SourceDocument(
                relative_path=relative_path,
                absolute_path=absolute_path,
                content=absolute_path.read_text(encoding="utf-8"),
            )
        )
    return sources


def _wrap_code_line(line: str, width: int) -> list[str]:
    expanded = line.expandtabs(4)
    if not expanded:
        return [""]
    return [expanded[index:index + width] for index in range(0, len(expanded), width)]


def _ascii_safe(text: str) -> str:
    return "".join(character if 32 <= ord(character) <= 126 else "?" for character in text)


def _pdf_escape(text: str) -> str:
    return (
        _ascii_safe(text)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _content_stream(commands: list[str]) -> bytes:
    return "\n".join(commands).encode("latin-1", errors="replace")


def _text_command(font: str, size: float, x: float, y: float, text: str) -> str:
    return f"BT /{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({_pdf_escape(text)}) Tj ET"


def _cover_pages(sources: list[SourceDocument]) -> list[bytes]:
    pages: list[bytes] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    commands = [
        _text_command("F2", 18.0, MARGIN, PAGE_HEIGHT - MARGIN - 10.0, "Companion App Splat Training Code Export"),
        _text_command(
            "F3",
            10.0,
            MARGIN,
            PAGE_HEIGHT - MARGIN - 30.0,
            "Curated code bundle for the companion app training flow.",
        ),
        _text_command("F3", 9.0, MARGIN, PAGE_HEIGHT - MARGIN - 48.0, f"Generated: {now}"),
        _text_command(
            "F3",
            9.0,
            MARGIN,
            PAGE_HEIGHT - MARGIN - 62.0,
            f"Included files: {len(sources)} | Total source lines: {sum(item.line_count for item in sources)}",
        ),
    ]

    y = PAGE_HEIGHT - MARGIN - 96.0
    commands.append(_text_command("F2", 12.0, MARGIN, y, "Files"))
    y -= 18.0
    for index, source in enumerate(sources, start=1):
        commands.append(
            _text_command(
                "F1",
                9.0,
                MARGIN,
                y,
                f"{index:02d}. {source.relative_path} ({source.line_count} lines)",
            )
        )
        y -= 12.0
        if y < (MARGIN + FOOTER_GAP + 16.0):
            pages.append(_content_stream(commands))
            commands = []
            y = PAGE_HEIGHT - MARGIN - 10.0
    commands.append(_text_command("F3", 8.0, MARGIN, MARGIN - 4.0, "Page 1"))
    pages.append(_content_stream(commands))
    return pages


def _file_pages(
    sources: list[SourceDocument],
    starting_page_number: int,
) -> list[bytes]:
    pages: list[bytes] = []
    page_number = starting_page_number
    top_y = PAGE_HEIGHT - MARGIN - HEADER_GAP - HEADER_FONT_SIZE
    bottom_y = MARGIN + FOOTER_GAP
    code_start_y = top_y - 28.0
    lines_per_page = int((code_start_y - bottom_y) // CODE_LEADING)

    for index, source in enumerate(sources, start=1):
        wrapped_lines: list[str] = []
        for line in source.content.splitlines():
            wrapped_lines.extend(_wrap_code_line(line, MAX_CODE_CHARS))
        if source.content.endswith("\n"):
            wrapped_lines.append("")
        if not wrapped_lines:
            wrapped_lines = [""]

        cursor = 0
        chunk_number = 0
        while cursor < len(wrapped_lines):
            chunk_number += 1
            commands = [
                _text_command(
                    "F2",
                    HEADER_FONT_SIZE,
                    MARGIN,
                    PAGE_HEIGHT - MARGIN - HEADER_FONT_SIZE,
                    f"File {index}/{len(sources)}: {source.relative_path}",
                ),
                _text_command(
                    "F3",
                    META_FONT_SIZE,
                    MARGIN,
                    PAGE_HEIGHT - MARGIN - HEADER_FONT_SIZE - 14.0,
                    f"{source.absolute_path} | page chunk {chunk_number}",
                ),
            ]

            y = code_start_y
            for _ in range(lines_per_page):
                if cursor >= len(wrapped_lines):
                    break
                commands.append(_text_command("F1", CODE_FONT_SIZE, MARGIN, y, wrapped_lines[cursor]))
                cursor += 1
                y -= CODE_LEADING

            commands.append(_text_command("F3", 8.0, MARGIN, MARGIN - 4.0, f"Page {page_number}"))
            pages.append(_content_stream(commands))
            page_number += 1

    return pages


def _build_pdf(page_streams: list[bytes]) -> bytes:
    objects: list[bytes] = []

    def add_object(payload: bytes | str) -> int:
        data = payload.encode("latin-1") if isinstance(payload, str) else payload
        objects.append(data)
        return len(objects)

    font_1 = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    font_2 = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    font_3 = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids: list[int] = []
    content_ids: list[int] = []
    for stream in page_streams:
        content_id = add_object(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
        content_ids.append(content_id)
        page_ids.append(0)

    pages_id = add_object("<< /Type /Pages /Kids [] /Count 0 >>")

    for page_index, content_id in enumerate(content_ids):
        page_id = add_object(
            (
                "<< /Type /Page"
                f" /Parent {pages_id} 0 R"
                f" /MediaBox [0 0 {PAGE_WIDTH:.0f} {PAGE_HEIGHT:.0f}]"
                f" /Contents {content_id} 0 R"
                f" /Resources << /Font << /F1 {font_1} 0 R /F2 {font_2} 0 R /F3 {font_3} 0 R >> >>"
                " >>"
            )
        )
        page_ids[page_index] = page_id

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    chunks = [header]
    offsets = [0]
    current_offset = len(header)
    for object_number, object_payload in enumerate(objects, start=1):
        offsets.append(current_offset)
        object_bytes = f"{object_number} 0 obj\n".encode("ascii") + object_payload + b"\nendobj\n"
        chunks.append(object_bytes)
        current_offset += len(object_bytes)

    xref_offset = current_offset
    xref_lines = [f"0 {len(objects) + 1}", "0000000000 65535 f "]
    for offset in offsets[1:]:
        xref_lines.append(f"{offset:010d} 00000 n ")
    trailer = (
        "xref\n"
        + "\n".join(xref_lines)
        + "\ntrailer\n"
        + f"<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        + "startxref\n"
        + f"{xref_offset}\n"
        + "%%EOF\n"
    ).encode("ascii")
    chunks.append(trailer)
    return b"".join(chunks)


def export_pdf(output_path: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    sources = _read_sources(repo_root)
    cover_pages = _cover_pages(sources)
    body_pages = _file_pages(sources, starting_page_number=len(cover_pages) + 1)

    all_pages = cover_pages + body_pages
    pdf_bytes = _build_pdf(all_pages)
    output_path.write_bytes(pdf_bytes)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the companion app splat training code to a PDF.")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_NAME,
        help="Output PDF path. Defaults to a file in the repository root.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = repo_root / output_path

    export_pdf(output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
