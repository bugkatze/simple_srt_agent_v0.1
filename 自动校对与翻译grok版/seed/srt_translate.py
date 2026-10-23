#!/usr/bin/env python3
"""Export SRT texts for translation and apply translations to create bilingual or monolingual subtitles.

Usage:
  python srt_translate.py export input.srt [--output translation_source.json]
  python srt_translate.py apply input.srt translation.json output.srt [--only-translation]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TIMESTAMP_RE = re.compile(
    r"^(\d{2,}):([0-5]\d):([0-5]\d),(\d{3}) --> "
    r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})$"
)

@dataclass
class Segment:
    segment_id: str
    index: str
    timestamp: str
    text: str

def parse_srt(content: str) -> list[Segment]:
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise ValueError("SRT 文件为空。")

    blocks = re.split(r"\n[ \t]*\n", content)
    segments: list[Segment] = []
    
    for ordinal, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) < 3:
            raise ValueError(f"第 {ordinal} 个字幕块不足 3 行。")
            
        index = lines[0].strip()
        timestamp = lines[1].strip()
        text = "\n".join(lines[2:])
        segments.append(Segment(f"s{ordinal:06d}", index, timestamp, text))
        
    return segments


def export_command(args: argparse.Namespace) -> int:
    content = Path(args.input).read_bytes().decode("utf-8-sig")
    segments = parse_srt(content)
    
    manifest = {
        "schema_version": 1,
        "source_file": Path(args.input).name,
        "segment_count": len(segments),
        "segments": [{"segment_id": s.segment_id, "original": s.text} for s in segments]
    }
    
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"成功导出 {len(segments)} 条待翻译文本至 {args.output}")
    else:
        sys.stdout.write(rendered)
    return 0


def apply_command(args: argparse.Namespace) -> int:
    content = Path(args.input).read_bytes().decode("utf-8-sig")
    segments = parse_srt(content)
    
    patch_data = json.loads(Path(args.translation).read_text(encoding="utf-8"))
    translations = patch_data.get("translations")
    
    if not isinstance(translations, dict):
        raise ValueError("翻译 JSON 的根节点必须包含一个 'translations' 字典对象。")
        
    output_blocks: list[str] = []
    translated_count = 0
    
    for segment in segments:
        translated_text = translations.get(segment.segment_id)
        
        if translated_text and translated_text.strip():
            # 方案切换：根据参数决定是否保留原文
            if args.only_translation:
                final_text = translated_text.strip()
            else:
                final_text = f"{translated_text.strip()}\n{segment.text}"
            translated_count += 1
        else:
            # 如果没有提供翻译，则退回保留纯原文，不报错以增加容错率
            final_text = segment.text
            
        output_blocks.append(f"{segment.index}\n{segment.timestamp}\n{final_text}")
        
    output_text = "\n\n".join(output_blocks) + "\n"
    output_path = Path(args.output)
    output_path.write_text(output_text, encoding="utf-8", newline="\n")
    
    mode = "纯译文" if args.only_translation else "双语"
    
    report = {
        "status": "completed",
        "mode": mode,
        "input": str(Path(args.input)),
        "output": str(output_path),
        "total_segments": len(segments),
        "translated_segments": translated_count
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export texts for translation and apply translations to create bilingual/monolingual SRT.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="导出待翻译文本结构")
    export_parser.add_argument("input", help="已完成校对的单语 SRT 文件")
    export_parser.add_argument("--output", help="输出的 JSON 源文件路径")
    export_parser.set_defaults(func=export_command)

    apply_parser = subparsers.add_parser("apply", help="应用翻译 JSON 生成双语或纯译文字幕")
    apply_parser.add_argument("input", help="已完成校对的单语 SRT 文件")
    apply_parser.add_argument("translation", help="包含翻译结果的 JSON 文件")
    apply_parser.add_argument("output", help="生成的双语 SRT 输出路径")
    apply_parser.add_argument("--only-translation", action="store_true", help="启用此标志则只保留译文，不保留原文")
    apply_parser.set_defaults(func=apply_command)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
