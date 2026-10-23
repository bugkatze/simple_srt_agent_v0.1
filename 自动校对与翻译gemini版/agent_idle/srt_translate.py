#!/usr/bin/env python3
"""
SRT 翻译提取与合成脚本 (自动化流水线版)

用法：
  python srt_translate.py export input.srt [--output to_translate.json] [--mirror-dir path/to/current]
  python srt_translate.py apply input.srt translation.json output.srt [--mirror-dir path/to/current]
"""

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# ==========================================
# 翻译输出模式控制 (全局变量)
# False: 保留原文，生成双语字幕 (译文在上，原文在下)
# True:  丢弃原文，仅保留纯译文
# ==========================================
ONLY_TRANSLATION = True

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

def mirror_file(filepath: Path | str, mirror_dir: str | None) -> None:
    """执行文件分叉"""
    if not mirror_dir:
        return
    src = Path(filepath)
    if src.exists():
        dst = Path(mirror_dir) / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[MIRROR] 复制 {src.name} -> {dst}")

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
    out_path = Path(args.output) if args.output else Path("translation_source.json")
    
    out_path.write_text(rendered, encoding="utf-8")
    print(f"成功导出 {len(segments)} 条待翻译文本至 {out_path.name}")
    
    # 导出完成后分叉
    mirror_file(out_path, args.mirror_dir)
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
            # 使用内部全局变量判断模式
            if ONLY_TRANSLATION:
                final_text = translated_text.strip()
            else:
                final_text = f"{translated_text.strip()}\n{segment.text}"
            translated_count += 1
        else:
            final_text = segment.text
            
        output_blocks.append(f"{segment.index}\n{segment.timestamp}\n{final_text}")
        
    output_text = "\n\n".join(output_blocks) + "\n"
    output_path = Path(args.output)
    output_path.write_text(output_text, encoding="utf-8", newline="\n")
    
    mode = "纯译文" if ONLY_TRANSLATION else "双语"
    print(f"合成完成 [{mode}]，共翻译 {translated_count}/{len(segments)} 条。")
    
    # 应用翻译后，将返回的 translation.json 和最终结果分叉
    mirror_file(args.translation, args.mirror_dir)
    mirror_file(output_path, args.mirror_dir)
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description="Export texts and apply translations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("input", help="已完成校对的单语 SRT 文件")
    export_parser.add_argument("--output", help="输出的 JSON 源文件路径")
    export_parser.add_argument("--mirror-dir", help="同步复制目录")
    export_parser.set_defaults(func=export_command)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("input", help="已完成校对的单语 SRT 文件")
    apply_parser.add_argument("translation", help="包含翻译结果的 JSON 文件")
    apply_parser.add_argument("output", help="生成的 SRT 输出路径")
    apply_parser.add_argument("--mirror-dir", help="同步复制目录")
    apply_parser.set_defaults(func=apply_command)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
