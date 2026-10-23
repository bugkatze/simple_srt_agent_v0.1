"""LoopAgent Gemini 网页适配层（JSON 提取版）

职责：
1. 使用 loop_agent_browser 提供的 Edge 浏览器能力
2. 打开并等待 Gemini 聊天页面就绪（支持检测已存在的标签页并切换焦点）
3. 接收 LoopAgent 已经渲染完成的最终 Prompt (或多 Batch Context)
4. 提交 Prompt 并监测 Gemini 生成状态
5. 提取生成的 JSON 代码块并保存（替换了原本的 Python 提取逻辑）
6. 提供兼容的文件上传接口
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from loop_agent_browser import EdgeBrowser


# ============================================================
# Gemini configuration
# ============================================================

GEMINI_URL = "https://gemini.google.com/app"

STARTUP_WAIT = 5
READY_TIMEOUT = 60
MAX_GENERATE_WAIT = 120
STABLE_POLLS_REQUIRED = 2

# Gemini 私有 selector
INPUT_SELECTOR = 'textarea, [contenteditable="true"]'


# ============================================================
# Gemini Session
# ============================================================

class GeminiSession:
    """
    Gemini 网站适配层。
    """

    def __init__(self):
        self.browser_session = EdgeBrowser(
            startup_wait=STARTUP_WAIT,
        )
        self.page = None
        self._connected = False

    # ========================================================
    # Browser lifecycle
    # ========================================================

    def start(self):
        if self._connected:
            print("[GEMINI] 会话已经启动")
            return

        print("[GEMINI] 启动 Edge / 连接浏览器...")
        self.browser_session.start()

        target_domain = "gemini.google.com"
        found_page = None
        
        if self.browser_session.browser:
            for _ in range(5):
                for context in self.browser_session.browser.contexts:
                    for page in context.pages:
                        try:
                            if target_domain in (page.url or "").lower():
                                found_page = page
                                break
                        except Exception:
                            continue
                    if found_page:
                        break
                if found_page:
                    break
                time.sleep(0.5)

        if found_page:
            print(f"[GEMINI] 检测到已存在的 Gemini 网页，转换焦点: {found_page.url}")
            self.page = found_page
            try:
                self.page.bring_to_front()
            except Exception as exc:
                print(f"[GEMINI] 焦点转换异常: {exc}")
            
            self.browser_session.page = found_page

            try:
                for context in self.browser_session.browser.contexts:
                    for p in context.pages:
                        if p != found_page and p.url in ("about:blank", "edge://newtab/"):
                            p.close()
            except Exception:
                pass
        else:
            print(f"[GEMINI] 未检测到现有标签页，跳转至 {GEMINI_URL} ...")
            self.page = self.browser_session.page
            self.page.goto(GEMINI_URL)

        if not self._wait_for_ready():
            self._save_debug_screenshot("gemini_debug_not_ready.png")
            self.browser_session.close()
            self.page = None
            raise RuntimeError("Gemini 页面在等待时间内未出现聊天输入框。")

        self._connected = True
        print(f"[GEMINI] 会话就绪: {self.page.url}")

    def _wait_for_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        deadline = time.time() + timeout
        announced = False

        while time.time() < deadline:
            try:
                url = (self.page.url or "").lower()
                if "login" in url or "signin" in url:
                    print("[GEMINI] 检测到登录页面，请手动完成登录...")

                input_box = self.page.locator(INPUT_SELECTOR).first
                if input_box.count() > 0 and input_box.is_visible():
                    return True
            except Exception:
                pass

            if not announced:
                print(f"[GEMINI] 等待聊天输入框加载 (最多 {timeout:.0f}s)...")
                announced = True

            time.sleep(0.5)

        return False

    def _ensure_connected(self):
        if not self._connected or self.page is None:
            raise RuntimeError("GeminiSession 未启动，请先调用 start()")

    # ========================================================
    # DOM state
    # ========================================================

    def get_response_indicators(self) -> int:
        self._ensure_connected()
        try:
            return self.page.evaluate(
                "() => document.querySelectorAll('thumb-up-button').length"
            )
        except Exception:
            return 0

    def get_stop_button_exists(self) -> bool:
        self._ensure_connected()
        try:
            return self.page.evaluate(
                "() => document.querySelectorAll('[data-mat-icon-name=\"stop\"]').length > 0"
            )
        except Exception:
            return False

    def _get_current_code_length(self) -> int:
        self._ensure_connected()
        try:
            return self.page.evaluate(
                """
                () => {
                    const responses = document.querySelectorAll('model-response');
                    if (!responses.length) return 0;
                    
                    const lastResponse = responses[responses.length - 1];
                    const blocks = lastResponse.querySelectorAll('code.code-container, pre > code');
                    
                    return blocks.length ? blocks[blocks.length - 1].innerText.length : 0;
                }
                """
            )
        except Exception:
            return 0

    def _extract_json_code(self) -> str:
        """从最新的回复中提取 JSON 代码块（原为 Python）"""
        self._ensure_connected()
        try:
            return self.page.evaluate(
                """
                () => {
                    const responses = document.querySelectorAll('model-response');
                    if (!responses.length) return '';
                    
                    const lastResponse = responses[responses.length - 1];
                    const selectors = [
                        'code.code-container',
                        'pre > code',
                        'code-block code',
                        'code'
                    ];

                    for (const selector of selectors) {
                        const blocks = lastResponse.querySelectorAll(selector);
                        if (!blocks.length) continue;
                        
                        // 寻找最后一个非空的代码块，假设大模型最后输出的是 JSON
                        for (let i = blocks.length - 1; i >= 0; i--) {
                             const text = blocks[i].innerText;
                             // 简单检查是否像是 JSON（以 { 开始）
                             if (text && text.trim().startsWith('{') && text.length > 10) {
                                 return text;
                             }
                        }
                        
                        // 如果没有看起来像 JSON 的，退而求其次返回最后一个长块
                        const lastBlock = blocks[blocks.length - 1];
                        const text = lastBlock.innerText;
                        if (text && text.length > 10) return text;
                    }
                    return '';
                }
                """
            )
        except Exception as e:
            print(f"[GEMINI] 提取 JSON 失败: {e}")
            return ""

    # ========================================================
    # Sending & Input
    # ========================================================

    def _get_input_box(self):
        self._ensure_connected()
        input_box = self.page.locator(INPUT_SELECTOR).first
        input_box.wait_for(state="visible", timeout=READY_TIMEOUT * 1000)
        return input_box

    def append_input_text(self, text: str) -> bool:
        self._ensure_connected()
        if not isinstance(text, str):
            raise TypeError(f"text 必须是 str，实际类型: {type(text).__name__}")

        try:
            input_box = self._get_input_box()
            input_box.evaluate(
                """
                (el, t) => {
                    el.focus();
                    document.execCommand('insertText', false, t);
                }
                """,
                text,
            )
            return True
        except Exception as exc:
            print(f"[GEMINI] 追加输入失败: {exc}")
            return False

    def send(self) -> bool:
        self._ensure_connected()
        try:
            input_box = self._get_input_box()
            input_box.press("Enter")
            return True
        except Exception as exc:
            print(f"[GEMINI] 提交发送失败: {exc}")
            return False

    def _wait_for_generation(self) -> bool:
        print("[GEMINI] 等待生成...")
        start_time = time.time()
        seen_stop = False
        thumb_increased = False
        initial_thumbs = self.get_response_indicators()

        while time.time() - start_time < MAX_GENERATE_WAIT:
            stop_exists = self.get_stop_button_exists()
            if stop_exists and not seen_stop:
                print("[GEMINI] 检测到生成开始")
                seen_stop = True

            current_thumbs = self.get_response_indicators()
            if current_thumbs > initial_thumbs:
                print(f"[GEMINI] 检测到新响应完成 ({initial_thumbs} -> {current_thumbs})")
                thumb_increased = True
                break

            if seen_stop and not stop_exists:
                print("[GEMINI] stop 按钮消失，确认响应完成...")
                time.sleep(0.5)
                if self.get_response_indicators() > initial_thumbs:
                    thumb_increased = True
                break

            time.sleep(0.2)

        if not thumb_increased:
            print("[GEMINI] 错误：生成未完成或失败")
            return False

        print("[GEMINI] 阶段2.5：确认代码块渲染完成...")
        last_code_len = 0
        stable_count = 0

        for _ in range(20):
            time.sleep(1.0)
            current_len = self._get_current_code_length()
            if current_len == last_code_len and current_len > 0:
                stable_count += 1
                if stable_count >= STABLE_POLLS_REQUIRED:
                    print(f"[GEMINI] 代码块稳定，长度: {current_len}")
                    break
            else:
                last_code_len = current_len
                stable_count = 0
        else:
            print("[GEMINI] 警告：代码块未完全稳定，继续尝试提取")

        time.sleep(2)
        return True

    # ========================================================
    # Generation
    # ========================================================

    def generate(self, prompt: str, task_idx: int = 0, workspace: str | None = None):
        self._ensure_connected()
        if workspace is None:
            workspace = os.environ.get("LOOP_AGENT_DATA_CURRENT", "agent_idle/file_database/current")

        print(f"[GEMINI] 发送任务 idx={task_idx}")
        
        try:
            input_box = self._get_input_box()
            input_box.fill("")
            self.append_input_text(prompt)
            self.send()
        except Exception as exc:
            print(f"[GEMINI] 输入/发送失败: {exc}")
            self._save_debug_screenshot("gemini_debug_send_failed.png")
            return None

        if not self._wait_for_generation():
            self._save_debug_screenshot("gemini_debug_timeout.png")
            return None

        # 核心改动：提取 JSON 而不是 Python
        code = self._extract_json_code().replace("\u00a0", " ")
        if not code.strip():
            self._save_debug_screenshot("gemini_debug_empty_code.png")
            print("[GEMINI] 提取的 JSON 为空")
            return None

        return self._save_json(code, task_idx, workspace)

    def generate_prepared(self, task_description: str, task_idx: int = 0, workspace: str | None = None):
        self._ensure_connected()
        if workspace is None:
            workspace = os.environ.get("LOOP_AGENT_DATA_CURRENT", "agent_idle/file_database/current")

        print(f"[GEMINI] 发送已准备好的 Context 输入 idx={task_idx}")
        
        if not self.send():
            self._save_debug_screenshot("gemini_debug_send_failed.png")
            return None

        if not self._wait_for_generation():
            self._save_debug_screenshot("gemini_debug_timeout.png")
            return None

        # 核心改动：提取 JSON
        code = self._extract_json_code().replace("\u00a0", " ")
        if not code.strip():
            self._save_debug_screenshot("gemini_debug_empty_code.png")
            print("[GEMINI] 提取的 JSON 为空")
            return None

        return self._save_json(code, task_idx, workspace)

    # ========================================================
    # Save generated json
    # ========================================================

    def _save_json(self, code: str, task_idx: int, workspace: str):
        """保存提取到的 JSON，不再添加 Python 风格的注释头"""
        workspace_path = Path(workspace)
        workspace_path.mkdir(parents=True, exist_ok=True)

        filename = f"{time.strftime('%Y%m%d')}_{task_idx:03d}.json"
        filepath = workspace_path / filename

        filepath.write_text(code, encoding="utf-8")
        print(f"[GEMINI] 已保存 JSON 资产: {filepath}")
        return filepath

    # ========================================================
    # Upload compatibility API
    # ========================================================

    def upload_file_via_clipboard(self, file_path: str) -> bool:
        if not self._connected:
            return False
        return self.browser_session.upload_file(file_path, input_selector=INPUT_SELECTOR)

    def upload_files_via_clipboard(self, file_paths: list[str]) -> bool:
        if not self._connected:
            return False
        return self.browser_session.upload_files(file_paths, input_selector=INPUT_SELECTOR)

    def upload_file(self, file_path: str) -> bool:
        return self.upload_file_via_clipboard(file_path)

    def upload_files(self, file_paths: list[str]) -> bool:
        return self.upload_files_via_clipboard(file_paths)

    # ========================================================
    # Diagnostics & Cleanup
    # ========================================================

    def _save_debug_screenshot(self, filename: str) -> None:
        try:
            if self.page:
                self.page.screenshot(path=filename, full_page=True)
                print(f"[GEMINI] 调试截图: {filename}")
        except Exception as exc:
            print(f"[GEMINI] 保存截图失败: {exc}")

    def close(self):
        try:
            self.browser_session.close()
        except Exception as exc:
            print(f"[GEMINI] 关闭浏览器失败: {exc}")
            
        self.page = None
        self._connected = False
        print("[GEMINI] 会话关闭")


__all__ = [
    "GeminiSession",
    "GEMINI_URL",
]
