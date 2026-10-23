LoopAgent SRT 自动化字幕流水线

这是一个基于大语言模型（Gemini / Grok）网页端驱动的自动化字幕校对与翻译系统（simple_agent_v0.2的branch）
本项目通过将“大模型认知”与“本地数据处理”彻底解耦，实现了一个稳定的多阶段状态机流水线。大模型仅作为纯粹的 JSON 数据生成器，而本地严格校验的 Python 脚本负责最终的 SRT 文件操作。

✨ 核心特性

双轨校验状态机：严格区分为“合并校对（Proofread）”与“提取翻译（Translate）”两大阶段。

 Schema 约束：通过上传本地执行脚本（srt_patch_v2.py 等）作为大模型的先验知识，免去冗长的 Prompt 示例。

强容错与自动反思：如果模型生成的 JSON 格式异常，本地沙盒会立即拦截并提取 stderr 报错，将其抛回给大模型进行定向自我修正。

物理隔离的文件流转：

input/：只读物料区。

agent_idle/：纯净的代码执行机房。

current/：用于 LLM 交互与文件分叉缓冲的中转站。

📂 目录结构

📁 项目根目录/

├── 📁 input/                # 用户放置初始物料（input.srt、音视频参考文件）

├── 📁 seed/                 # 供大模型阅读的参考脚本（如 srt_patch_v2.py）

├── 📁 prompt_set/           # 极简指令集 (proofread_first.txt, translate_first.txt, error_retry.txt)

├── 📁 agent_idle/           # 脚本执行的工作沙盒

│   ├── srt_patch_v2.py      # 本地校对/合并脚本

│   ├── srt_translate.py     # 本地导出/翻译合成脚本

│   └── 📁 file_database/  

│       ├── 📁 current/      # 运行时产生的中间件与分叉缓冲

│       └── 📁 data_history/ # 任务完成后的时间戳自动归档区

├── srt_core.py              # 核心状态机调度器

├── srt_support.py           # 工作区与存档管理器

├── srt_gemini.py            # Gemini 网页适配与 JSON 提取器

├── srt_grok.py              # Grok 网页适配与 JSON 提取器

├── loop_agent_browser.py    # 浏览器底层控制层 (Playwright/CDP)

├── srt_use_gemini.py      # Gemini 版启动器

└── srt_use_grok.py        # Grok 版启动器


🚀 快速开始

1. 前置准备

确保已安装 Python 3.12+。

安装必要的依赖（与simple_agent_v0.2所用相同）。

建议提前登录好 Gemini 或 Grok 的网页端。

2. 准备物料

将初版识别的字幕文件重命名为 input.srt，连同参考的音频或视频文件（推荐使用低码率音频以节省上传时间），放入 input/ 文件夹中。

3. 运行流水线

在命令行中运行对应的启动器：

使用 Gemini 引擎：

python srt_use_gemini.py


使用 Grok 引擎：

python  srt_use_grok.py


⚙️ 工作流程详解

初始化 (Init Stage)：系统自动将 seed/ 中的脚本上传，让大模型学习 JSON 格式约束；随后上传 input/ 中的字幕与音视频物料。

合并与校对 (Proofread Stage)：模型输出包含 update 和 merge 动作的 JSON 补丁。本地 srt_patch_v2.py 执行严苛校验并生成 cleaned.srt，结果自动分叉至缓冲槽。

提取与翻译 (Translate Stage)：本地 srt_translate.py 从清洗后的字幕中导出纯文本字典（绕过冗余的 SRT 格式），通过 Context 直接追加给大模型。模型返回纯译文 JSON，本地再次合成最终的双语 SRT。

归档 (Finalize Stage)：所有中间产物与最终结果（包括补丁 JSON 和双语 SRT）会被统一打包，按时间戳归档存入 data_history/。

🔧 自定义配置

单语/双语切换：在 agent_idle/srt_translate.py 顶部，修改 ONLY_TRANSLATION = False/True 即可一键切换输出双语或纯译文字幕。

建议与videocaptioner一起使用，未来可能会研发全自动版本
