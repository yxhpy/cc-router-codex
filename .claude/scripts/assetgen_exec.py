#!/usr/bin/env python3
"""Codex-only raster image asset generator.

This is the only image-generation entry point used by the taskctl assetgen
role. It delegates generation to the local Codex CLI, verifies that requested
outputs are real raster files, and optionally writes a local asset manifest.
It intentionally has no DALL-E/OpenAI image API path and no SVG fallback.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import codex_exec
import prompt_template_mcp


RASTER_FORMATS = {"png", "jpg", "jpeg", "webp"}
ASSET_ROLES = {
    "game": (
        "Create production-ready game art with a clear silhouette, reusable "
        "shapes, clean edges, and strong readability at small sizes."
    ),
    "web": (
        "Create a polished website or app visual asset with clean composition, "
        "responsive-crop friendly framing, and useful negative space."
    ),
    "video": (
        "Create cinematic key art, a thumbnail, a storyboard frame, or a video "
        "visual asset with a strong focal point and readable framing."
    ),
    "other": (
        "Create a polished reusable image asset suitable for downstream design "
        "or production work."
    ),
}


class AssetgenError(RuntimeError):
    """Raised when the asset generator cannot produce verified raster files."""


@dataclass(frozen=True)
class OutputImage:
    index: int
    path: Path
    format: str


def now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on", "fast"}


def progress_heartbeat_seconds() -> int:
    value = _parse_int(os.environ.get("ASSETGEN_PROGRESS_HEARTBEAT_SECONDS"))
    if value is None:
        return 60
    return max(0, value)


class AssetgenProgress:
    """Best-effort task DB progress writer for long-running asset generation."""

    def __init__(self, db_path: Path | None, job_id: int | None, task_id: int | None) -> None:
        self.db_path = db_path
        self.job_id = job_id
        self.task_id = task_id

    @classmethod
    def from_env(cls) -> "AssetgenProgress":
        raw_db = os.environ.get("TASKCTL_DB", "").strip()
        db_path = Path(raw_db) if raw_db else None
        return cls(
            db_path,
            _parse_int(os.environ.get("TASKCTL_JOB_ID")),
            _parse_int(os.environ.get("TASKCTL_TASK_ID")),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.db_path and self.db_path.is_file())

    def emit(self, message: str, *, event_type: str = "assetgen_progress") -> None:
        if not self.enabled:
            return
        stamp = now_stamp()
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute(
                "INSERT INTO events(job_id, task_id, type, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (self.job_id, self.task_id, event_type, message, stamp),
            )
            if self.task_id is not None:
                try:
                    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (stamp, self.task_id))
                except sqlite3.Error:
                    pass
            conn.commit()
        except (OSError, sqlite3.Error):
            return
        finally:
            if conn is not None:
                conn.close()


def resolve_workspace(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise AssetgenError(f"workspace is not a directory: {path}")
    return path


def resolve_inside_workspace(workspace: Path, value: str, *, label: str) -> Path:
    if not value or not str(value).strip():
        raise AssetgenError(f"{label} path is required")
    root = workspace.expanduser().resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AssetgenError(f"{label} path escapes workspace: {value}") from exc
    return resolved


def raster_format_for_path(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "jpeg":
        return "jpg"
    if suffix not in RASTER_FORMATS:
        allowed = ", ".join(sorted(RASTER_FORMATS - {"jpeg"}))
        raise AssetgenError(f"assetgen outputs must be raster files ({allowed}); got {path.name}")
    return suffix


def output_images(workspace: Path, values: Iterable[str]) -> list[OutputImage]:
    images: list[OutputImage] = []
    seen: set[Path] = set()
    for value in values:
        path = resolve_inside_workspace(workspace, value, label="output")
        if path in seen:
            continue
        seen.add(path)
        images.append(OutputImage(index=len(images) + 1, path=path, format=raster_format_for_path(path)))
    if not images:
        raise AssetgenError("assetgen requires at least one --output raster path")
    return images


def verify_raster(path: Path, image_format: str) -> None:
    if not path.is_file():
        raise AssetgenError(f"expected image file was not created: {path}")
    data = path.read_bytes()
    if not data:
        raise AssetgenError(f"generated image file is empty: {path}")
    if image_format == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssetgenError(f"generated file is not a PNG image: {path}")
    if image_format == "jpg" and not data.startswith(b"\xff\xd8\xff"):
        raise AssetgenError(f"generated file is not a JPEG image: {path}")
    if image_format == "webp" and not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise AssetgenError(f"generated file is not a WebP image: {path}")


def all_outputs_valid(images: Sequence[OutputImage]) -> tuple[bool, str]:
    try:
        for image in images:
            verify_raster(image.path, image.format)
    except AssetgenError as exc:
        return False, str(exc)
    return True, ""


def build_output_schema(count: int) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "images": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "index": {"type": "integer"},
                        "path": {"type": "string"},
                        "format": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["index", "path", "format", "description"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["images", "notes"],
    }


def asset_prompt(prompt: str, asset_role: str) -> str:
    role = ASSET_ROLES.get(asset_role or "other", ASSET_ROLES["other"])
    return (
        f"{role}\n\n"
        "User request:\n"
        f"{prompt.strip()}\n\n"
        "Requirements:\n"
        "- Generate reusable production image assets, not decorative placeholders.\n"
        "- Do not write SVG, HTML, CSS, or vector-only assets.\n"
        "- Do not hotlink remote images.\n"
        "- The final files must be real raster files at the exact requested paths.\n"
        "- Avoid rendering text inside images unless explicitly requested by the user."
    )


def build_request_analysis(prompt: str, images: Sequence[OutputImage], *, size: str, style: str, asset_role: str) -> str:
    targets = ", ".join(f"{image.path.name}:{image.format}" for image in images)
    style_text = style.strip() if style and style.strip() else "derive visual style from the user request and retrieved templates"
    return (
        f"- Asset role: {asset_role or 'other'}.\n"
        f"- Output count and formats: {len(images)} file(s), {targets}.\n"
        f"- Target size: {size}.\n"
        f"- Style source: {style_text}.\n"
        f"- User intent summary: {prompt.strip()}"
    )


def build_codex_prompt(
    prompt: str,
    images: Sequence[OutputImage],
    *,
    size: str,
    style: str,
    asset_role: str,
    template_context: str,
    fast: bool = False,
) -> str:
    targets = "\n".join(f"{image.index}. {image.path} ({image.format})" for image in images)
    style_line = style.strip() if style and style.strip() else "Use the user request and asset role as the style source."
    if fast:
        return f"""Create exactly {len(images)} production raster image file(s).

{asset_prompt(prompt, asset_role)}

Outputs:
{targets}

Fast generation contract:
- Prompt-template MCP skipped: retrieval was skipped for lower latency.
- Write real {", ".join(sorted({image.format for image in images}))} raster data at the exact requested path(s).
- Target size: {size}
- Style guidance: {style_line}
- Do not create SVG, HTML, CSS, placeholders, or alternate filenames.
- Respond only with JSON matching the supplied schema after the file(s) exist.
"""
    return f"""You are a Codex image asset backend.

Create exactly {len(images)} raster image file(s) for this asset request:

Requirement analysis:
{build_request_analysis(prompt, images, size=size, style=style, asset_role=asset_role)}

Retrieved prompt-template context:
{template_context}

{asset_prompt(prompt, asset_role)}

Write the file(s) exactly here:
{targets}

Generation contract:
- Use the available Codex image-generation capability or another Codex-controlled raster-writing method.
- First adapt the retrieved prompt-template patterns into one concrete generation prompt for the requested asset.
- Target size: {size}
- Style guidance: {style_line}
- Do not create SVG files or vector placeholders.
- Do not create a different filename, extension, or directory.
- If you cannot create valid raster image files, leave the files absent so verification fails.
- After writing the files, respond only with JSON matching the supplied schema.
"""


def parse_json_object(text: str) -> Mapping[str, Any]:
    cleaned = (text or "").strip()
    if not cleaned:
        raise AssetgenError("Codex did not return a JSON final message")
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise AssetgenError("Codex final message did not contain JSON") from None
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise AssetgenError("Codex final message was not a JSON object")
    return parsed


def verify_payload(
    payload: Mapping[str, Any],
    images: Sequence[OutputImage],
    workspace: Path,
    *,
    progress: AssetgenProgress | None = None,
) -> None:
    items = payload.get("images")
    if not isinstance(items, list):
        raise AssetgenError("Codex JSON payload is missing images")
    if len(items) != len(images):
        raise AssetgenError(f"Codex returned {len(items)} image item(s), expected {len(images)}")
    for expected, item in zip(images, items):
        if not isinstance(item, Mapping):
            raise AssetgenError(f"image item {expected.index} is not an object")
        declared = resolve_inside_workspace(workspace, str(item.get("path") or ""), label="declared image")
        if declared != expected.path:
            raise AssetgenError(f"Codex declared unexpected image path: {declared}")
        verify_raster(expected.path, expected.format)
        if progress:
            progress.emit(
                "verified image %s/%s: %s"
                % (expected.index, len(images), expected.path.relative_to(workspace).as_posix())
            )


def build_command(
    *,
    codex_bin: str,
    workspace: Path,
    output_dirs: Sequence[Path],
    schema_path: Path,
    message_path: Path,
    model: str,
    reasoning_effort: str,
    sandbox: str,
) -> list[str]:
    resolved_sandbox, _ = codex_exec._resolve_sandbox_mode(sandbox)
    cmd = [
        codex_bin,
        "exec",
        "--sandbox",
        resolved_sandbox,
        "--skip-git-repo-check",
        "-C",
        str(workspace),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(message_path),
        "--color",
        "never",
        "--ephemeral",
    ]
    for directory in output_dirs:
        cmd.extend(["--add-dir", str(directory)])
    if model:
        cmd.extend(["--model", model])
    if reasoning_effort:
        effort, _ = codex_exec._normalize_reasoning_effort(reasoning_effort)
        if effort:
            cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])
    cmd.append("-")
    return cmd


def _heartbeat_progress(
    progress: AssetgenProgress,
    stop: threading.Event,
    *,
    interval_seconds: int,
    message: str,
) -> None:
    started = time.monotonic()
    while not stop.wait(interval_seconds):
        elapsed = int(time.monotonic() - started)
        progress.emit(f"{message}; elapsed={elapsed}s")


def run_codex(
    command: Sequence[str],
    prompt: str,
    *,
    timeout: int,
    progress: AssetgenProgress | None = None,
    heartbeat_seconds: int | None = None,
    heartbeat_message: str = "codex generation still running",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    codex_exec._apply_proxy_env(env)
    stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    if progress and progress.enabled and heartbeat_seconds and heartbeat_seconds > 0:
        heartbeat_thread = threading.Thread(
            target=_heartbeat_progress,
            args=(progress, stop),
            kwargs={"interval_seconds": heartbeat_seconds, "message": heartbeat_message},
            daemon=True,
        )
        heartbeat_thread.start()
    try:
        return subprocess.run(
            list(command),
            input=prompt,
            cwd=None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    finally:
        stop.set()
        if heartbeat_thread:
            heartbeat_thread.join(timeout=1)


def write_manifest(
    path: Path,
    workspace: Path,
    images: Sequence[OutputImage],
    *,
    prompt: str,
    size: str,
    style: str,
    prompt_template_mcp: Mapping[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "backend": "codex",
        "generated_at": now_stamp(),
        "prompt": prompt,
        "size": size,
        "style": style,
        "prompt_template_mcp": dict(prompt_template_mcp or {}),
        "images": [
            {
                "index": image.index,
                "path": image.path.relative_to(workspace).as_posix(),
                "format": image.format,
                "bytes": image.path.stat().st_size,
            }
            for image in images
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate raster image assets through Codex only.")
    parser.add_argument("--workspace", required=True, help="Target project workspace.")
    parser.add_argument("--prompt", required=True, help="Asset prompt.")
    parser.add_argument("--output", action="append", required=True, help="Raster output path inside the workspace.")
    parser.add_argument("--manifest", help="Optional local_asset_manifest JSON path inside the workspace.")
    parser.add_argument("--asset-role", choices=sorted(ASSET_ROLES), default="other")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--style", default="")
    parser.add_argument("--model", default=os.environ.get("ASSETGEN_CODEX_MODEL") or os.environ.get("CODEX_MODEL", "gpt-5.4-mini"))
    parser.add_argument("--reasoning-effort", default=os.environ.get("CODEX_REASONING_EFFORT", ""))
    parser.add_argument("--sandbox", default=os.environ.get("ASSETGEN_CODEX_SANDBOX", "workspace"))
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", ""))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("ASSETGEN_CODEX_TIMEOUT", "900")))
    parser.add_argument("--prompt-template-top", type=int, default=int(os.environ.get("ASSETGEN_PROMPT_TEMPLATE_TOP", "3")))
    parser.add_argument("--fast", action="store_true", default=env_enabled("ASSETGEN_FAST"), help="Skip prompt-template MCP retrieval and use a compact generation prompt.")
    parser.add_argument("--reuse-existing", action="store_true", default=env_enabled("ASSETGEN_REUSE_EXISTING"), help="Return existing valid raster outputs without starting Codex.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    progress = AssetgenProgress.from_env()
    try:
        workspace = resolve_workspace(args.workspace)
        images = output_images(workspace, args.output or [])
        manifest = (
            resolve_inside_workspace(workspace, args.manifest, label="manifest")
            if args.manifest
            else None
        )
        target_text = ", ".join(image.path.relative_to(workspace).as_posix() for image in images)
        progress.emit(f"requested {len(images)} raster image(s): {target_text}")
        if args.reuse_existing:
            valid, reason = all_outputs_valid(images)
            if valid:
                if manifest:
                    write_manifest(
                        manifest,
                        workspace,
                        images,
                        prompt=args.prompt,
                        size=args.size,
                        style=args.style,
                        prompt_template_mcp={"mode": "reuse_existing", "skipped": True},
                    )
                    progress.emit(f"wrote manifest: {manifest.relative_to(workspace).as_posix()}")
                progress.emit(f"assetgen reused existing outputs: {len(images)} image(s)")
                print("SUCCESS")
                for image in images:
                    print(image.path.relative_to(workspace).as_posix())
                if manifest:
                    print(f"MANIFEST: {manifest.relative_to(workspace).as_posix()}")
                return 0
            progress.emit(f"reuse-existing skipped: {reason}")
        if args.fast or args.prompt_template_top <= 0:
            progress.emit("prompt-template MCP skipped for fast assetgen")
            prompt_context = {
                "text": "Prompt-template MCP skipped by fast assetgen mode.",
                "metadata": {"mode": "fast", "skipped_prompt_template_mcp": True},
            }
        else:
            prompt_context = prompt_template_mcp.build_asset_prompt_context(
                workspace,
                args.prompt,
                asset_role=args.asset_role,
                size=args.size,
                style=args.style,
                top=max(1, args.prompt_template_top),
            )
        prompt_metadata = prompt_context.get("metadata") if isinstance(prompt_context, Mapping) else {}
        prompt_status = prompt_metadata.get("status") if isinstance(prompt_metadata, Mapping) else {}
        prompt_version = prompt_status.get("version") if isinstance(prompt_status, Mapping) else {}
        if isinstance(prompt_version, Mapping) and prompt_version.get("warning"):
            print(f"WARNING: {prompt_version['warning']}", file=sys.stderr)
            progress.emit(f"prompt-template warning: {prompt_version['warning']}")
        for image in images:
            image.path.parent.mkdir(parents=True, exist_ok=True)
            if image.path.exists():
                image.path.unlink()

        codex_bin = args.codex_bin.strip() or codex_exec._find_codex()
        output_dirs = sorted({image.path.parent for image in images}, key=lambda item: str(item))
        with tempfile.TemporaryDirectory(prefix=".assetgen_", dir=str(workspace)) as tmp:
            tmp_path = Path(tmp)
            schema_path = tmp_path / "assetgen_schema.json"
            message_path = tmp_path / "assetgen_last_message.json"
            schema_path.write_text(json.dumps(build_output_schema(len(images)), ensure_ascii=False), encoding="utf-8")
            prompt = build_codex_prompt(
                args.prompt,
                images,
                size=args.size,
                style=args.style,
                asset_role=args.asset_role,
                template_context=str(prompt_context.get("text") or ""),
                fast=bool(args.fast or args.prompt_template_top <= 0),
            )
            command = build_command(
                codex_bin=codex_bin,
                workspace=workspace,
                output_dirs=output_dirs,
                schema_path=schema_path,
                message_path=message_path,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                sandbox=args.sandbox,
            )
            progress.emit(f"codex generation started for {len(images)} image(s); timeout={args.timeout}s")
            result = run_codex(
                command,
                prompt,
                timeout=args.timeout,
                progress=progress,
                heartbeat_seconds=progress_heartbeat_seconds(),
                heartbeat_message=f"codex generation still running for {len(images)} image(s)",
            )
            progress.emit(f"codex generation exited {result.returncode}")
            final_text = message_path.read_text(encoding="utf-8", errors="replace") if message_path.exists() else result.stdout
            if result.returncode != 0:
                raise AssetgenError(
                    "Codex image generation failed with exit %s\nstdout:\n%s\nstderr:\n%s"
                    % (result.returncode, result.stdout.strip(), result.stderr.strip())
                )
            verify_payload(parse_json_object(final_text), images, workspace, progress=progress)
        if manifest:
            write_manifest(
                manifest,
                workspace,
                images,
                prompt=args.prompt,
                size=args.size,
                style=args.style,
                prompt_template_mcp=prompt_metadata if isinstance(prompt_metadata, Mapping) else {},
            )
            progress.emit(f"wrote manifest: {manifest.relative_to(workspace).as_posix()}")
        progress.emit(f"assetgen complete: {len(images)} image(s)")
    except (
        AssetgenError,
        prompt_template_mcp.PromptTemplateMcpError,
        OSError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as exc:
        progress.emit(f"assetgen failed: {exc}", event_type="assetgen_error")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("SUCCESS")
    for image in images:
        print(image.path.relative_to(workspace).as_posix())
    if manifest:
        print(f"MANIFEST: {manifest.relative_to(workspace).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
