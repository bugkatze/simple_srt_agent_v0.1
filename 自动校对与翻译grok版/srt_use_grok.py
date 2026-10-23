"""
SRT 自动化流水线启动器 (Grok 专版)

用法：
    python run_srt_pipeline_grok.py
"""

from pathlib import Path
import sys

# 确保能导入同级目录的模块
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入核心调度器与 Grok 适配层
from srt_core import LoopAgent
from srt_grok import GrokSession

def main():
    print("[RUNNER] 初始化 Grok Session...")
    session = GrokSession()

    print("[RUNNER] 初始化 SRT 调度引擎...")
    agent = LoopAgent(
        session=session,
        runtime_workspace=PROJECT_ROOT / "agent_idle",
        project_root=PROJECT_ROOT
    )

    # 启动浏览器并连接 Grok 网页
    agent.start()

    print("[RUNNER] 启动字幕双轨校对流水线 (Grok)...")
    try:
        # 执行纯净的字幕流水线
        result = agent.run_subtitle_pipeline(
            max_iter=3  # 单个阶段的失败重试最高次数
        )
        print(f"\n[RUNNER] 任务结束，最终状态: {result.get('status')}")
        
        # 打印被拦截的本地脚本报错（如果有的话），防止“死得不明不白”
        if "error" in result:
            print(f"\n[ERROR DETAILS]\n{result['error']}")
            
    finally:
        # 无论成功或异常，最后安全关闭浏览器释放资源
        agent.close()

    print("[RUNNER] 运行完毕。")

if __name__ == "__main__":
    main()
