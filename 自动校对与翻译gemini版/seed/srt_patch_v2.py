#!/usr/bin/env python3
"""Inspect SRT subtitles and apply JSON text and merge patches.

Usage:
  python srt_patch_v2.py inspect input.srt [--output manifest.json]
  python srt_patch_v2.py apply input.srt patch.json output.srt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TIMESTAMP_RE = re.compile(
    r"^(\d{2,}):([0-5]\d):([0-5]\d),(\d{3}) --> "
    r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})$"
)
SEGMENT_ID_RE = re.compile(r"^s\d{6,}$")


@dataclass
class Segment:
    segment_id: str
    index: str
    timestamp: str
    start_ms: int
    end_ms: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "index": self.index,
            "timestamp": self.timestamp,
            "text": self.text,
        }


def timestamp_ms(parts: tuple[str, ...]) -> tuple[int, int]:
    h1, m1, s1, ms1, h2, m2, s2, ms2 = parts
    start = (((int(h1) * 60 + int(m1)) * 60 + int(s1)) * 1000 + int(ms1))
    end = (((int(h2) * 60 + int(m2)) * 60 + int(s2)) * 1000 + int(ms2))
    return start, end

def ms_to_timestamp(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(content: str) -> list[Segment]:
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise ValueError("SRT 文件为空。")

    blocks = re.split(r"\n[ \t]*\n", content)
    segments: list[Segment] = []
    seen_indices: set[str] = set()

    for ordinal, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) < 3:
            raise ValueError(f"第 {ordinal} 个字幕块不足 3 行。")

        index = lines[0].strip()
        if not index.isdigit():
            raise ValueError(f"第 {ordinal} 个字幕块的序号不是整数：{index!r}")
        if index in seen_indices:
            raise ValueError(f"SRT 中存在重复序号：{index}")
        seen_indices.add(index)

        timestamp = lines[1].strip()
        match = TIMESTAMP_RE.fullmatch(timestamp)
        if not match:
            raise ValueError(
                f"字幕块 {index} 的时间戳格式错误：{timestamp!r}"
            )
        start_ms, end_ms = timestamp_ms(match.groups())
        if end_ms <= start_ms:
            raise ValueError(f"字幕块 {index} 的结束时间必须晚于开始时间。")

        text = "\n".join(lines[2:])
        if not text.strip():
            raise ValueError(f"字幕块 {index} 的字幕文本为空。")

        segments.append(
            Segment(
                segment_id=f"s{ordinal:06d}",
                index=index,
                timestamp=timestamp,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        )

    return segments


def read_srt(path: Path) -> tuple[str, list[Segment], str]:
    raw = path.read_bytes()
    content = raw.decode("utf-8-sig")
    segments = parse_srt(content)
    digest = hashlib.sha256(raw).hexdigest()
    return content, segments, digest


def inspect_command(args: argparse.Namespace) -> int:
    _, segments, digest = read_srt(Path(args.input))
    manifest = {
        "schema_version": 2,
        "source_file": Path(args.input).name,
        "source_sha256": digest,
        "segment_count": len(segments),
        "segments": [segment.to_dict() for segment in segments],
    }
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


def apply_command(args: argparse.Namespace) -> int:
    _, segments, digest = read_srt(Path(args.input))
    patch = json.loads(Path(args.patch).read_text(encoding="utf-8"))

    if not isinstance(patch, dict) or patch.get("schema_version") not in (1, 2):
        raise ValueError("patch JSON 必须是对象，且 schema_version 必须为 1 或 2。")

    expected_digest = patch.get("source_sha256")
    if expected_digest is not None and expected_digest != digest:
        raise ValueError(
            "source_sha256 不匹配：补丁可能对应另一份或已变化的 SRT。"
        )

    by_id = {segment.segment_id: segment for segment in segments}
    changes = patch.get("changes")
    if not isinstance(changes, list):
        raise ValueError("patch JSON 的 changes 必须是数组。")

    planned_updates: dict[str, Segment] = {}
    planned_deletions: set[str] = set()
    changed_ids: set[str] = set()
    
    # Parse and validate changes
    for number, change in enumerate(changes, start=1):
        if not isinstance(change, dict):
            raise ValueError(f"changes[{number - 1}] 必须是对象。")
            
        action = change.get("action", "update") # default to update for v1 compatibility

        if action == "update":
            segment_id = change.get("segment_id")
            if not segment_id or segment_id not in by_id:
                raise ValueError(f"找不到字幕 ID：{segment_id}")
            if segment_id in changed_ids:
                raise ValueError(f"同一补丁重复修改字幕：{segment_id}")
                
            segment = by_id[segment_id]
            original = change.get("original")
            replacement = change.get("replacement")
            
            if original != segment.text:
                raise ValueError(f"{segment_id} 的 original 与源字幕不一致。")
            if not replacement or not replacement.strip():
                raise ValueError(f"{segment_id} 的 replacement 不能为空。")
                
            changed_ids.add(segment_id)
            planned_updates[segment_id] = Segment(
                segment_id=segment.segment_id,
                index=segment.index,
                timestamp=segment.timestamp,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=replacement
            )

        elif action == "merge":
            segment_ids = change.get("segment_ids")
            if not isinstance(segment_ids, list) or len(segment_ids) < 2:
                raise ValueError(f"merge 动作需要包含至少2个 ID 的 segment_ids 数组。")
            
            originals = change.get("originals")
            if not isinstance(originals, list) or len(originals) != len(segment_ids):
                raise ValueError(f"merge 动作的 originals 必须与 segment_ids 长度一致。")
                
            replacement = change.get("replacement")
            if not replacement or not replacement.strip():
                raise ValueError(f"merge 的 replacement 不能为空。")

            # Validate IDs and Originals
            first_idx = -1
            for i, sid in enumerate(segment_ids):
                if sid not in by_id:
                    raise ValueError(f"找不到字幕 ID：{sid}")
                if sid in changed_ids:
                    raise ValueError(f"重复修改字幕：{sid}")
                
                seg = by_id[sid]
                if seg.text != originals[i]:
                    raise ValueError(f"{sid} 的 original 与源字幕不一致。")
                    
                # Check continuity
                curr_idx = segments.index(seg)
                if first_idx == -1:
                    first_idx = curr_idx
                elif curr_idx != first_idx + i:
                    raise ValueError(f"merge 的字幕块必须在原始 SRT 中是连续的。")
                
                changed_ids.add(sid)
            
            # Create merged segment
            first_seg = by_id[segment_ids[0]]
            last_seg = by_id[segment_ids[-1]]
            new_timestamp = f"{ms_to_timestamp(first_seg.start_ms)} --> {ms_to_timestamp(last_seg.end_ms)}"
            
            planned_updates[first_seg.segment_id] = Segment(
                segment_id=first_seg.segment_id,
                index=first_seg.index,
                timestamp=new_timestamp,
                start_ms=first_seg.start_ms,
                end_ms=last_seg.end_ms,
                text=replacement
            )
            
            for sid in segment_ids[1:]:
                planned_deletions.add(sid)

        else:
            raise ValueError(f"不支持的 action: {action}")

    # Rebuild SRT
    output_blocks: list[str] = []
    new_index = 1
    
    for segment in segments:
        if segment.segment_id in planned_deletions:
            continue
            
        target_seg = planned_updates.get(segment.segment_id, segment)
        output_blocks.append(f"{new_index}\n{target_seg.timestamp}\n{target_seg.text}")
        new_index += 1

    output_text = "\n\n".join(output_blocks) + "\n"
    output_path = Path(args.output)
    output_path.write_text(output_text, encoding="utf-8", newline="\n")

    report = {
        "status": "completed",
        "input": str(Path(args.input)),
        "output": str(output_path),
        "source_sha256": digest,
        "changes_applied": len(changes),
        "validation": {
            "all_targets_exist": True,
            "original_text_matches": True,
            "no_duplicate_targets": True,
            "continuity_checked": True
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect SRT and apply JSON text and merge patches.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="输出字幕清单和源文件 SHA-256")
    inspect_parser.add_argument("input", help="输入 SRT 文件")
    inspect_parser.add_argument("--output", help="清单 JSON 输出路径；省略则输出到终端")
    inspect_parser.set_defaults(func=inspect_command)

    apply_parser = subparsers.add_parser("apply", help="校验并应用 JSON 字幕补丁")
    apply_parser.add_argument("input", help="原始 SRT 文件")
    apply_parser.add_argument("patch", help="校对补丁 JSON")
    apply_parser.add_argument("output", help="修改后 SRT 输出路径")
    apply_parser.set_defaults(func=apply_command)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
