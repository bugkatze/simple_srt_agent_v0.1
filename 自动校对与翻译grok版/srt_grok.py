"""LoopAgent Grok 网页适配层（JSON 提取 & 停止按钮强稳定版）

职责：
1. 使用 loop_agent_browser 提供的 Edge 浏览器能力
2. 打开并等待 Grok 聊天页面就绪
3. 接收 LoopAgent 渲染完成的 Prompt 或大文本 Context
4. 提交任务，并基于“停止模型响应”按钮的出现与消失来严格判定生成状态
5. 智能提取生成的 JSON 代码块并纯净保存
6. 提供兼容的文件上传接口
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from loop_agent_browser import EdgeBrowser

# ============================================================
# Grok configuration
# ============================================================

GROK_URL = "https://grok.com/"

# ============================================================
# Runtime parameters
# ============================================================

STARTUP_WAIT = 5
READY_TIMEOUT = 60
MAX_GENERATE_WAIT = 300
ASSISTANT_APPEAR_TIMEOUT = 30
STOP_GONE_STABLE_POLLS = 2
POLL_INTERVAL = 1.0

# ============================================================
# Grok Session
# ============================================================

class GrokSession:
    """
    Grok 网站适配层。
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
            print("[GROK] 会话已经启动")
            return

        print("[GROK] 启动 Edge / 连接浏览器...")
        self.page = self.browser_session.start(GROK_URL)
        print(f"[GROK] DOMContentLoaded: {self.page.url}")

        if not self._wait_for_chat_or_login():
            self._save_debug_screenshot("grok_debug_not_ready.png")
            self.browser_session.close()
            self.page = None
            raise RuntimeError("Grok 页面未进入可用聊天状态")

        self._connected = True
        print(f"[GROK] 会话就绪: {self.page.url}")

    def _has_login_page(self) -> bool:
        if not self.page:
            return False
        url = (self.page.url or "").lower()
        return "/login" in url or "/signin" in url or "/sign-in" in url

    def _wait_for_chat_or_login(self, timeout: float = READY_TIMEOUT) -> bool:
        if not self.page:
            return False
        deadline = time.time() + timeout
        announced = False

        while time.time() < deadline:
            try:
                if self._has_login_page():
                    print("[GROK] 检测到登录页面，请在浏览器中完成登录...")
                    time.sleep(1)
                    continue

                editor = self.page.locator(
                    '[data-testid="chat-input"] [contenteditable="true"], '
                    'div[aria-label="Ask Grok anything"][contenteditable="true"], '
                    '[contenteditable="true"]'
                ).first

                if editor.count() > 0 and editor.is_visible():
                    print("[GROK] 聊天输入框已就绪")
                    return True
            except Exception:
                pass

            if not announced:
                print(f"[GROK] 等待聊天界面加载 （最多 {timeout:.0f}s）...")
                announced = True

            time.sleep(0.5)

        return False

    def _ensure_connected(self) -> None:
        if not self._connected or self.page is None:
            raise RuntimeError("GrokSession 未启动，请先调用 start()")

    # ========================================================
    # DOM helpers
    # ========================================================

    def get_assistant_count(self) -> int:
        self._ensure_connected()
        return int(
            self.page.evaluate(
                """() => document.querySelectorAll('[data-testid="assistant-message"]').length"""
            )
        )

    def get_code_block_count(self) -> int:
        self._ensure_connected()
        return int(
            self.page.evaluate(
                """() => document.querySelectorAll('[data-testid="code-block"]').length"""
            )
        )

    def get_stop_button_count(self) -> int:
        self._ensure_connected()
        return int(
            self.page.evaluate(
                """() => document.querySelectorAll('button[aria-label="Stop model response"], button[aria-label="停止模型响应"]').length"""
            )
        )

    def get_generating_state(self) -> bool:
        return self.get_stop_button_count() > 0

    def _get_last_assistant_signature(self) -> dict:
        self._ensure_connected()
        return self.page.evaluate(
            """
            () => {
                const messages = document.querySelectorAll('[data-testid="assistant-message"]');
                if (!messages.length) {
                    return { exists: false, messageLength: 0, codeLength: 0, codeBlockCount: 0, text: '' };
                }

                const message = messages[messages.length - 1];
                const codeBlocks = message.querySelectorAll('[data-testid="code-block"] pre code');
                let codeText = '';
                
                if (codeBlocks.length) {
                    const lastCode = codeBlocks[codeBlocks.length - 1];
                    codeText = lastCode.innerText || lastCode.textContent || '';
                }

                const text = message.innerText || message.textContent || '';
                return {
                    exists: true,
                    messageLength: text.length,
                    codeLength: codeText.length,
                    codeBlockCount: codeBlocks.length,
                    text: text
                };
            }
            """
        )

    def _get_input_box(self, timeout: float = READY_TIMEOUT):
        self._ensure_connected()
        selector = (
            '[data-testid="chat-input"] [contenteditable="true"], '
            'div[aria-label="Ask Grok anything"][contenteditable="true"], '
            '[contenteditable="true"]'
        )
        try:
            locator = self.page.locator(selector).first
            locator.wait_for(state="visible", timeout=int(timeout * 1000))
            return locator
        except Exception as exc:
            print(f"[GROK] 输入框未就绪: {exc}")
            return None

    def _has_new_response(self, initial_assistant: int, initial_code: int) -> bool:
        try:
            return (
                self.get_assistant_count() > initial_assistant or
                self.get_code_block_count() > initial_code
            )
        except Exception:
            return False

    # ========================================================
    # Sending & Input
    # ========================================================

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
            print(f"[GROK] 追加输入失败: {exc}")
            return False

    def send(self) -> bool:
        self._ensure_connected()
        try:
            input_box = self._get_input_box()
            input_box.press("Enter")
            return True
        except Exception as exc:
            print(f"[GROK] 提交发送失败: {exc}")
            return False

    def _wait_for_generation(self, initial_assistant: int, initial_codes: int) -> bool:
        start_time = time.time()
        
        print("[GROK] 阶段1: 等待生成开始 (检测停止按钮)...")
        stop_seen = False
        deadline = start_time + ASSISTANT_APPEAR_TIMEOUT
        
        while time.time() < deadline:
            try:
                if self.get_generating_state():
                    stop_seen = True
                    print("[GROK] 检测到生成状态 (停止按钮已出现)")
                    break
                # 作为兜底：如果没抓到按钮，但气泡或代码块增加了，也算开始
                if self._has_new_response(initial_assistant, initial_codes):
                    print("[GROK] 检测到新回复气泡")
                    break
            except Exception:
                pass
            time.sleep(0.5)
            
        if not stop_seen:
            print("[GROK] 警告: 未明显检测到停止按钮，继续侦测...")

        print("[GROK] 阶段2: 等待生成完成 (等待停止按钮消失)...")
        stable_count = 0
        
        while time.time() - start_time < MAX_GENERATE_WAIT:
            try:
                generating = self.get_generating_state()
                
                if generating:
                    stable_count = 0
                else:
                    stable_count += 1
                    
                # 如果停止按钮消失，且保持稳定 2 次轮询（约2秒），则认为生成彻底结束
                if not generating and stable_count >= STOP_GONE_STABLE_POLLS:
                    signature = self._get_last_assistant_signature()
                    print(
                        f"[GROK] 生成完成: message_len={signature.get('messageLength',0)}, "
                        f"code_len={signature.get('codeLength',0)}, "
                        f"blocks={signature.get('codeBlockCount',0)}"
                    )
                    return True
            except Exception:
                pass
            
            elapsed = time.time() - start_time
            if int(elapsed) > 0 and int(elapsed) % 15 == 0:
                print(f"[GROK] 仍在生成... {int(elapsed)}s")
                
            time.sleep(POLL_INTERVAL)
            
        print("[GROK] 达到最大等待时间，强制提取当前结果")
        return True

    # ========================================================
    # Generation
    # ========================================================

    def generate(self, prompt: str, task_idx: int = 0, workspace: str | None = None):
        self._ensure_connected()
        if workspace is None:
            workspace = os.environ.get("LOOP_AGENT_DATA_CURRENT", "agent_idle/file_database/current")

        print(f"[GROK] 发送任务 idx={task_idx}")
        initial_assistant = self.get_assistant_count()
        initial_codes = self.get_code_block_count()
        
        try:
            input_box = self._get_input_box()
            input_box.fill("")
            self.append_input_text(prompt)
            self.send()
        except Exception as exc:
            print(f"[GROK] 输入/发送失败: {exc}")
            self._save_debug_screenshot("grok_debug_send_failed.png")
            return None

        if not self._wait_for_generation(initial_assistant, initial_codes):
            self._save_debug_screenshot("grok_debug_timeout.png")
            return None

        return self._extract_and_save(task_idx, workspace)

    def generate_prepared(self, task_description: str, task_idx: int = 0, workspace: str | None = None):
        self._ensure_connected()
        if workspace is None:
            workspace = os.environ.get("LOOP_AGENT_DATA_CURRENT", "agent_idle/file_database/current")

        print(f"[GROK] 发送已准备好的 Context 输入 idx={task_idx}")
        initial_assistant = self.get_assistant_count()
        initial_codes = self.get_code_block_count()
        
        if not self.send():
            self._save_debug_screenshot("grok_debug_send_failed.png")
            return None

        if not self._wait_for_generation(initial_assistant, initial_codes):
            self._save_debug_screenshot("grok_debug_timeout.png")
            return None

        return self._extract_and_save(task_idx, workspace)

    def _extract_and_save(self, task_idx: int, workspace: str):
        time.sleep(1)
        code = self._extract_json_code().replace("\u00a0", " ")

        if not code.strip():
            self._save_debug_screenshot("grok_debug_empty_code.png")
            print("[GROK] 提取的 JSON 为空")
            return None

        return self._save_json(code, task_idx, workspace)

    # ========================================================
    # Code extraction
    # ========================================================

    def _extract_json_code(self) -> str:
        """从最新的回复中提取类似 JSON 的代码块"""
        self._ensure_connected()
        return self.page.evaluate(
            """
            () => {
                const messages = document.querySelectorAll('[data-testid="assistant-message"]');
                if (!messages.length) return '';

                const message = messages[messages.length - 1];
                const blocks = message.querySelectorAll('[data-testid="code-block"]');
                if (!blocks.length) return '';

                for (let i = blocks.length - 1; i >= 0; i--) {
                    const block = blocks[i];
                    const code = block.querySelector('pre code');
                    if (!code) continue;

                    const text = (code.innerText || code.textContent || '').trim();
                    if ((text.startsWith('{') || text.startsWith('[')) && text.length > 10) {
                        return text;
                    }
                }

                const last = blocks[blocks.length - 1];
                const pre = last.querySelector('pre code');
                return pre ? (pre.innerText || pre.textContent || '') : '';
            }
            """
        ) or ""

    # ========================================================
    # Save generated json
    # ========================================================

    def _save_json(self, code: str, task_idx: int, workspace: str):
        """保存为纯粹的 .json 文件，不加任何 Python 注释头"""
        workspace_path = Path(workspace)
        workspace_path.mkdir(parents=True, exist_ok=True)

        filename = f"{time.strftime('%Y%m%d')}_{task_idx:03d}.json"
        filepath = workspace_path / filename

        filepath.write_text(code, encoding="utf-8")
        print(f"[GROK] 已保存 JSON 资产: {filepath}")
        return filepath

    # ========================================================
    # Upload compatibility API
    # ========================================================

    def upload_file(self, file_path: str) -> bool:
        if not self._connected:
            return False
        return self.browser_session.upload_file(file_path, input_selector='[contenteditable="true"]')

    def upload_files(self, file_paths: list[str]) -> bool:
        if not self._connected:
            return False
        return self.browser_session.upload_files(file_paths, input_selector='[contenteditable="true"]')

    def upload_file_via_clipboard(self, file_path: str) -> bool:
        return self.upload_file(file_path)

    def upload_files_via_clipboard(self, file_paths: list[str]) -> bool:
        return self.upload_files(file_paths)

    # ========================================================
    # Diagnostics & Cleanup
    # ========================================================

    def _save_debug_screenshot(self, filename: str) -> None:
        try:
            if self.page:
                self.page.screenshot(path=filename, full_page=True)
                print(f"[GROK] 调试截图: {filename}")
        except Exception as exc:
            print(f"[GROK] 保存截图失败: {exc}")

    def close(self):
        try:
            self.browser_session.close()
        except Exception as exc:
            print(f"[GROK] 浏览器关闭失败: {exc}")
        self.page = None
        self._connected = False
        print("[GROK] 会话关闭")

__all__ = ["GrokSession", "GROK_URL"]
