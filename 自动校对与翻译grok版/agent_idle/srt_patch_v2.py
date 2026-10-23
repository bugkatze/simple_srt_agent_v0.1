#!/usr/bin/env python3
"""
SRT 校对与补丁合并脚本 (自动化流水线版)

用法：
  python srt_patch_v2.py apply input.srt patch.json output.srt [--mirror-dir path/to/current]
"""

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Segment:
    segment_id: str
    index: str
    timestamp: str
    text: str

def parse_srt(content: str) -> list[Segment]:
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return []
    
    blocks = re.split(r"\n[ \t]*\n", content)
    segments = []
    for ordinal, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) >= 3:
            segments.append(Segment(
                segment_id=f"s{ordinal:06d}",
                index=lines[0].strip(),
                timestamp=lines[1].strip(),
                text="\n".join(lines[2:])
            ))
    return segments

def mirror_file(filepath: Path | str, mirror_dir: str | None) -> None:
    """如果指定了 mirror_dir，则将文件复制过去（分叉）"""
    if not mirror_dir:
        return
    src = Path(filepath)
    if src.exists():
        dst = Path(mirror_dir) / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[MIRROR] 复制 {src.name} -> {dst}")

def apply_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    patch_path = Path(args.patch)
    output_path = Path(args.output)

    # 1. 解析原始 SRT
    segments = parse_srt(input_path.read_text(encoding="utf-8-sig"))
    seg_map = {s.segment_id: s for s in segments}

    # 2. 解析 JSON 补丁
    try:
        patch_data = json.loads(patch_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"JSON 解析失败: {exc}", file=sys.stderr)
        return 1

    actions = patch_data.get("actions", [])
    
    # 3. 执行修改逻辑 (简化版示例，根据你的实际逻辑扩展)
    for action in actions:
        act_type = action.get("action")
        target_ids = action.get("target_ids", [])
        new_text = action.get("text", "")
        
        if not target_ids:
            continue
            
        if act_type == "update" and len(target_ids) == 1:
            sid = target_ids[0]
            if sid in seg_map:
                seg_map[sid].text = new_text
                
        elif act_type == "merge" and len(target_ids) > 1:
            first_id = target_ids[0]
            last_id = target_ids[-1]
            if first_id in seg_map and last_id in seg_map:
                # 提取合并后的时间轴：第一个的开始 -> 最后一个的结束
                start_time = seg_map[first_id].timestamp.split(" --> ")[0]
                end_time = seg_map[last_id].timestamp.split(" --> ")[1]
                seg_map[first_id].timestamp = f"{start_time} --> {end_time}"
                seg_map[first_id].text = new_text
                
                # 清理被合并的项
                for sid in target_ids[1:]:
                    if sid in seg_map:
                        seg_map[sid].text = "" # 标记为删除

    # 4. 重新生成并排序 SRT
    output_blocks = []
    valid_index = 1
    for s in segments:
        if s.text.strip(): # 过滤掉被 merge 删除的空段落
            output_blocks.append(f"{valid_index}\n{s.timestamp}\n{s.text}")
            valid_index += 1

    output_path.write_text("\n\n".join(output_blocks) + "\n", encoding="utf-8")
    print(f"成功应用 {len(actions)} 条补丁至 {output_path.name}")

    # 5. 执行分叉逻辑
    mirror_file(patch_path, args.mirror_dir)
    mirror_file(output_path, args.mirror_dir)
    
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description="Apply JSON patch to SRT.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("input", help="原始 SRT 文件")
    apply_parser.add_argument("patch", help="模型生成的补丁 JSON")
    apply_parser.add_argument("output", help="输出的清洗后 SRT")
    apply_parser.add_argument("--mirror-dir", help="将补丁和输出文件复制到指定目录 (如 current/)")
    apply_parser.set_defaults(func=apply_command)

    args = parser.parse_args()
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
