"""LoopAgent Core (双轨校对流水线 - 纯净调度版)

文件流转规则：
- seed/: 仅存放供 LLM 阅读的参考代码（约束 Schema）。
- input/: 仅存放初始 SRT 和视频（供 LLM 了解上下文）。
- agent_idle/: 代码执行的纯净机房，需提前放置好运行脚本。
- current/: 结果分叉区与上传缓冲区，归档时被清空。分叉动作由外部脚本自行完成。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from srt_support import FileDatabase, RuntimeWorkspace


# ============================================================
# Project Resources & Prompts
# ============================================================

class ProjectResources:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parent
        self.seed = self.root / "seed"
        self.input = self.root / "input"
        self.prompt_set = self.root / "prompt_set"

    def ensure(self) -> None:
        self.seed.mkdir(parents=True, exist_ok=True)
        self.input.mkdir(parents=True, exist_ok=True)
        self.prompt_set.mkdir(parents=True, exist_ok=True)


class PromptSet:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parent / "prompt_set"
        self._cache: dict[str, str] = {}

    def load(self, filename: str) -> str:
        if filename in self._cache:
            return self._cache[filename]
        path = self.root / filename
        if not path.is_file():
            raise FileNotFoundError(f"Prompt 文件不存在: {path}")
        content = path.read_text(encoding="utf-8")
        self._cache[filename] = content
        return content

    def render(self, filename: str, **variables: Any) -> str:
        template = self.load(filename)
        for key, val in variables.items():
            template = template.replace("{{" + key + "}}", str(val))
        return template.strip()


# ============================================================
# LoopAgent
# ============================================================

class LoopAgent:
    def __init__(
        self,
        session,
        runtime_workspace: str | Path | None = None,
        prompt_set: str | Path | None = None,
        project_root: str | Path | None = None,
    ):
        self.project = ProjectResources(project_root)
        self.project.ensure()
        self.runtime = RuntimeWorkspace(runtime_workspace)
        self.runtime.ensure()
        
        self.prompt_set = PromptSet(prompt_set)
        self.file_db = FileDatabase(self.runtime.file_database)
        self.session = session

    def start(self):
        self.session.start()

    def close(self):
        self.session.close()

    def _run_local_script(self, script_name: str, args: list[str]) -> tuple[bool, str]:
        """直接在 agent_idle 执行预置好的脚本，文件分叉等行为由脚本自身参数控制"""
        script_path = self.runtime.root / script_name
        if not script_path.exists():
            return False, f"机房 (agent_idle) 缺少必需的执行脚本: {script_name}"

        command = [sys.executable, str(script_path)] + args
        try:
            print(f"[EXEC] 运行: {' '.join([script_name] + args)}")
            process = subprocess.run(
                command,
                cwd=str(self.runtime.root),
                capture_output=True,
                text=True,
                timeout=30
            )
            if process.returncode == 0:
                return True, process.stdout
            else:
                return False, process.stderr or process.stdout
        except Exception as exc:
            return False, f"脚本执行异常: {exc}"

    # --- 核心流水线 ---

    def run_subtitle_pipeline(self, max_iter: int = 3) -> dict:
        print("\n" + "#" * 60)
        print("# LoopAgent 自动化双轨校对流水线 (纯净调度版) 启动")
        print("#" * 60)

        # ==========================================
        # 阶段一：极简初始化
        # ==========================================
        print("\n>>> 阶段一：资源初始化")
        
        seed_files = [p for p in self.project.seed.glob("*.py") if p.is_file()]
        if seed_files:
            print(f"[SEED] 上传 {len(seed_files)} 个参考脚本...")
            self.session.upload_files([str(p.resolve()) for p in seed_files])

        input_srt_src = self.project.input / "input.srt"
        if not input_srt_src.exists():
            print("[ERROR] input/ 目录下未检测到 input.srt。")
            return {"status": "missing_input"}
            
        # 将原始 SRT 放入机房供脚本调用
        shutil.copy2(input_srt_src, self.runtime.root / "input.srt")
        
        input_files = [p for p in self.project.input.glob("*") if p.is_file()]
        if input_files:
            print(f"[INPUT] 上传 {len(input_files)} 个源文件 (视频/SRT)...")
            self.session.upload_files([str(p.resolve()) for p in input_files])

        mirror_arg = ["--mirror-dir", str(self.runtime.current)]

        # ==========================================
        # 阶段二：校对与整理 (Proofread)
        # ==========================================
        print("\n>>> 阶段二：合并与校对")
        patch_success = False
        current_prompt = self.prompt_set.render("proofread_first.txt")
        
        for iteration in range(max_iter):
            print(f"[PROOFREAD] 第 {iteration + 1} 轮生成...")
            saved_file = self.session.generate(current_prompt, task_idx=iteration, workspace=str(self.runtime.root))
            
            if not saved_file:
                current_prompt = self.prompt_set.render("error_retry.txt", ERROR="未能提取到 JSON，请重试。")
                continue

            Path(saved_file).rename(self.runtime.root / "patch.json")

            # 将 current 路径作为参数传给脚本，由脚本自行完成 patch.json 和 cleaned.srt 的双向写入
            ok, output = self._run_local_script("srt_patch_v2.py", ["apply", "input.srt", "patch.json", "cleaned.srt"] + mirror_arg)
            
            if ok:
                print(f"[PROOFREAD] 校对成功！\n{output}")
                patch_success = True
                break
            else:
                print(f"[PROOFREAD] 校对失败 (返厂): \n{output}")
                current_prompt = self.prompt_set.render("error_retry.txt", ERROR=output[:2000])

        if not patch_success:
            return {"status": "proofread_failed"}

        # ==========================================
        # 阶段三：翻译转换 (Translate)
        # ==========================================
        print("\n>>> 阶段三：提取与翻译")
        
        ok, output = self._run_local_script("srt_translate.py", ["export", "cleaned.srt", "--output", "to_translate.json"] + mirror_arg)
        if not ok:
            return {"status": "export_failed", "error": output}
            
        manifest_text = (self.runtime.root / "to_translate.json").read_text(encoding="utf-8")
        
        self.session.append_input_text(f"请翻译以下文本清单：\n\n{manifest_text}")
        current_prompt = self.prompt_set.render("translate_first.txt")
        translate_success = False

        for iteration in range(max_iter):
            print(f"[TRANSLATE] 第 {iteration + 1} 轮生成...")
            saved_file = self.session.generate_prepared(current_prompt, task_idx=iteration, workspace=str(self.runtime.root))
            
            if not saved_file:
                current_prompt = self.prompt_set.render("error_retry.txt", ERROR="未能提取到 JSON，请重试。")
                continue

            Path(saved_file).rename(self.runtime.root / "translation.json")

            ok, output = self._run_local_script("srt_translate.py", ["apply", "cleaned.srt", "translation.json", "final_bilingual.srt"] + mirror_arg)
            
            if ok:
                print(f"[TRANSLATE] 双语合成成功！\n{output}")
                translate_success = True
                break
            else:
                print(f"[TRANSLATE] 合成失败 (返厂): \n{output}")
                current_prompt = self.prompt_set.render("error_retry.txt", ERROR=output[:2000])

        if not translate_success:
             return {"status": "translate_failed"}

        # ==========================================
        # 阶段四：产出与归档
        # ==========================================
        print("\n>>> 阶段四：产出与归档")
        
        archived_paths = self.file_db.archive_current()
        print(f"[FINALIZE] 任务完成，已将 {len(archived_paths)} 个快照存入 data_history。")

        return {
            "status": "success",
            "archived": [str(p) for p in archived_paths]
        }
