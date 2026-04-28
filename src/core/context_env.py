import os
import time
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import json
import ast
import subprocess
from receipts import get_receipt_service, create_receipt, ActionType, CognitionTier, ReceiptStatus

logger = logging.getLogger(__name__)

# Configuration
MAX_CONTEXT_TOKENS = 128000  # Default safe limit
MAX_FILES_IN_CONTEXT = 50
MAX_CHAT_HISTORY_MESSAGES = int(os.getenv("LANCELOT_CHAT_HISTORY_MAX_MESSAGES", "200"))
CHAT_HISTORY_RECENT_KEEP = int(os.getenv("LANCELOT_CHAT_HISTORY_RECENT_KEEP", "120"))
CHAT_HISTORY_COMPACT_BATCH = int(os.getenv("LANCELOT_CHAT_HISTORY_COMPACT_BATCH", "40"))
MAX_CHAT_SUMMARIES = int(os.getenv("LANCELOT_CHAT_SUMMARIES_MAX", "40"))
CHAT_SUMMARY_PREVIEW_CHARS = 280
BOOTSTRAP_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "config" / "bootstrap"
BOOTSTRAP_DATA_FILES = ("RULES.md", "CAPABILITIES.md")

@dataclass
class ContextItem:
    """Represents a single item in the context window."""
    id: str
    type: str  # 'file', 'memory', 'system'
    content: str
    tokens: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
class ContextEnvironment:
    """Manages the agent's context window deterministically and safely.
    
    Replaces RAG with explicit, receipt-tracked file loading.
    """
    
    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self._ensure_bootstrap_files()
        self.receipt_service = get_receipt_service(data_dir)
        self.items: Dict[str, ContextItem] = {}
        self.history: List[Dict[str, Any]] = []
        self.chat_summaries: List[Dict[str, Any]] = []
        self.current_tokens = 0
        self._current_quest_id: Optional[str] = None  # Set by orchestrator per chat() call
        self._load_chat_summaries()
        self._load_history()

    def set_current_quest_id(self, quest_id: Optional[str]) -> None:
        """Attach future context receipts to the active chat quest."""
        self._current_quest_id = quest_id

    def _ensure_bootstrap_files(self):
        """Seed canonical runtime text files from tracked templates when missing."""
        for filename in BOOTSTRAP_DATA_FILES:
            destination = Path(self.data_dir) / filename
            if destination.exists():
                continue
            template = BOOTSTRAP_TEMPLATE_DIR / filename
            if not template.exists():
                continue
            try:
                destination.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception as e:
                logger.warning("Error seeding bootstrap file %s: %s", filename, e)
        
    def _chat_dir(self) -> str:
        """Return (and create) a dedicated chat subdirectory the librarian won't move."""
        d = os.path.join(self.data_dir, "chat")
        os.makedirs(d, exist_ok=True)
        return d

    def _load_history(self):
        """Loads chat history from JSON."""
        history_path = os.path.join(self._chat_dir(), "chat_log.json")
        if os.path.exists(history_path):
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.history = loaded if isinstance(loaded, list) else []
            except Exception as e:
                logger.warning("Error loading chat history: %s", e)

    def _chat_summaries_path(self) -> str:
        return os.path.join(self._chat_dir(), "chat_summaries.json")

    def _load_chat_summaries(self):
        """Loads deterministic summaries of compacted chat history."""
        summaries_path = self._chat_summaries_path()
        if os.path.exists(summaries_path):
            try:
                with open(summaries_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.chat_summaries = loaded if isinstance(loaded, list) else []
            except Exception as e:
                logger.warning("Error loading chat summaries: %s", e)

    def save_chat_summaries(self):
        """Persists deterministic summaries of compacted chat history."""
        summaries_path = self._chat_summaries_path()
        try:
            with open(summaries_path, "w", encoding="utf-8") as f:
                json.dump(self.chat_summaries[-MAX_CHAT_SUMMARIES:], f, indent=2)
        except Exception as e:
            logger.warning("Error saving chat summaries: %s", e)

    def save_history(self):
        """Persists chat history to JSON."""
        history_path = os.path.join(self._chat_dir(), "chat_log.json")
        try:
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
             logger.warning("Error saving chat history: %s", e)

    def add_history(self, role: str, content: str):
        """Adds a message to history and auto-saves."""
        self.history.append({"role": role, "content": content, "timestamp": time.time()})
        self._compact_history_if_needed()
        self.save_history()

    def get_history_string(self, limit: int = 50, channel: str = None) -> str:
        """Formats recent chat history for context, optionally filtered by channel.

        When channel is specified, only include messages from that channel.
        (tagged as "[via <channel>]") plus assistant responses and untagged API messages.
        This prevents cross-channel pollution (e.g. War Room health checks pushing
        Telegram conversation out of the context window).
        """
        summaries = self._filtered_chat_summaries(channel)[-8:]

        if not self.history and not summaries:
            return ""

        buffer = []

        if summaries:
            buffer.append("--- COMPACTED CHAT HISTORY ---")
            for summary in summaries:
                timestamp = self._format_summary_timestamp(summary)
                buffer.append(f"SUMMARY [{timestamp}]: {summary.get('summary', '')}")

        if not self.history:
            return "\n".join(buffer)

        buffer.append("--- RECENT CHAT HISTORY ---")

        if channel:
            # Filter to messages from this channel
            filtered = []
            for msg in self.history:
                content = msg.get("content", "")
                role = msg.get("role", "")
                if role == "assistant":
                    filtered.append(msg)
                elif f"[via {channel}]" in content:
                    filtered.append(msg)
                elif role == "user" and "[via " not in content:
                    # API messages (no channel tag) — include as shared context
                    filtered.append(msg)
            recent = filtered[-limit:]
        else:
            recent = self.history[-limit:]

        for msg in recent:
            # S10: Sanitize output? No, trusting internal history for now, but role is critical.
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            # Truncate content for sanity if massive
            if len(content) > 4000:
                content = content[:4000] + "... [TRUNCATED]"
            buffer.append(f"{role}: {content}")
            
        return "\n".join(buffer)

    def _compact_history_if_needed(self):
        if len(self.history) <= MAX_CHAT_HISTORY_MESSAGES:
            return

        recent_keep = min(CHAT_HISTORY_RECENT_KEEP, MAX_CHAT_HISTORY_MESSAGES)
        older_messages = self.history[:-recent_keep]
        if not older_messages:
            return

        batch_size = max(1, CHAT_HISTORY_COMPACT_BATCH)
        for start in range(0, len(older_messages), batch_size):
            batch = older_messages[start:start + batch_size]
            self.chat_summaries.append(self._summarize_chat_batch(batch))

        self.chat_summaries = self.chat_summaries[-MAX_CHAT_SUMMARIES:]
        self.history = self.history[-recent_keep:]
        self.save_chat_summaries()

    def _summarize_chat_batch(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        user_messages = [m for m in messages if m.get("role") == "user"]
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]
        channels = sorted({ch for msg in messages for ch in self._message_channels(msg)})

        first_ts = messages[0].get("timestamp") if messages else None
        last_ts = messages[-1].get("timestamp") if messages else None
        user_preview = "; ".join(
            self._preview_chat_text(msg.get("content", ""), CHAT_SUMMARY_PREVIEW_CHARS)
            for msg in user_messages[:3]
        )
        assistant_preview = "; ".join(
            self._preview_chat_text(msg.get("content", ""), CHAT_SUMMARY_PREVIEW_CHARS)
            for msg in assistant_messages[:3]
        )

        parts = [
            f"Compacted {len(messages)} older chat messages",
            f"{len(user_messages)} user / {len(assistant_messages)} assistant",
        ]
        if channels:
            parts.append(f"channels: {', '.join(channels)}")
        if user_preview:
            parts.append(f"user intents: {user_preview}")
        if assistant_preview:
            parts.append(f"assistant outcomes: {assistant_preview}")

        return {
            "created_at": time.time(),
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "message_count": len(messages),
            "channels": channels,
            "source": "deterministic_chat_compaction",
            "summary": ". ".join(parts),
        }

    def _filtered_chat_summaries(self, channel: str = None) -> List[Dict[str, Any]]:
        if not channel:
            return self.chat_summaries

        summaries = []
        for summary in self.chat_summaries:
            channels = summary.get("channels") or []
            if not channels or channel in channels or "shared" in channels:
                summaries.append(summary)
        return summaries

    @staticmethod
    def _message_channels(message: Dict[str, Any]) -> List[str]:
        content = message.get("content", "") or ""
        channels = re.findall(r"\[via ([^\]]+)\]", content)
        if channels:
            return channels
        if message.get("role") == "user":
            return ["shared"]
        return []

    @staticmethod
    def _preview_chat_text(value: Any, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 16)].rstrip() + "... [truncated]"

    @staticmethod
    def _format_summary_timestamp(summary: Dict[str, Any]) -> str:
        timestamp = summary.get("last_timestamp") or summary.get("created_at")
        if isinstance(timestamp, (int, float)):
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
        return "unknown"
        
    def _is_safe_path(self, path: str) -> bool:
        """Ensures path is within data_dir."""
        # Simple traversal check
        abs_path = os.path.abspath(path)
        return os.path.commonpath([abs_path, self.data_dir]) == self.data_dir

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation (4 chars/token)."""
        return len(text) // 4

    def read_file(self, file_path: str, parent_id: Optional[str] = None) -> Optional[str]:
        """Reads a file into context, generating a trace receipt.
        
        Args:
            file_path: Relative or absolute path to file
            parent_id: Optional parent receipt ID
            
        Returns:
            The file content if successful, None if failed/blocked.
        """
        # Resolve path
        if not os.path.isabs(file_path):
            full_path = os.path.join(self.data_dir, file_path)
        else:
            full_path = file_path
            
        # Security Check
        if not self._is_safe_path(full_path):
            # Create rejection receipt
            self._log_rejection(file_path, "Path Traversal Blocked", parent_id)
            return None
            
        if not os.path.exists(full_path):
            self._log_rejection(file_path, "File Not Found", parent_id)
            return None
            
        # Limits Check
        if len(self.items) >= MAX_FILES_IN_CONTEXT:
             self._log_rejection(file_path, "Max Context Files Reached", parent_id)
             return None
             
        # Create Receipt
        receipt = create_receipt(
            ActionType.FILE_OP,
            "read_context",
            {"path": file_path},
            tier=CognitionTier.DETERMINISTIC,
            parent_id=parent_id,
            quest_id=self._current_quest_id,
        )
        self.receipt_service.create(receipt)
        
        start_time = time.time()
        
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                
            tokens = self._estimate_tokens(content)
            
            # Token Limit Check
            if self.current_tokens + tokens > MAX_CONTEXT_TOKENS:
                duration = int((time.time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail("Context Token Limit Exceeded", duration))
                return None
            
            # Success - Add to Context
            item = ContextItem(
                id=file_path,
                type="file",
                content=content,
                tokens=tokens,
                metadata={"path": full_path}
            )
            self.items[file_path] = item
            self.current_tokens += tokens
            
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(
                receipt.complete(
                    {"size_bytes": len(content), "tokens": tokens}, 
                    duration, 
                    token_count=tokens
                )
            )
            
            return content
            
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.fail(str(e), duration))
            return None

    def _log_rejection(self, path: str, reason: str, parent_id: Optional[str]):
        """Helper to log failed reads."""
        receipt = create_receipt(
            ActionType.FILE_OP,
            "read_context_blocked",
            {"path": path, "reason": reason},
            tier=CognitionTier.DETERMINISTIC,
            parent_id=parent_id,
            quest_id=self._current_quest_id,
        )
        self.receipt_service.create(receipt)
        self.receipt_service.update(receipt.complete({"status": "blocked"}, 0))

    def clear(self):
        """Clears current context."""
        self.items = {}
        self.current_tokens = 0

    def get_recent_receipts(self, limit: int = 10) -> str:
        """Fetches recent receipts formatted for context."""
        try:
            receipts = self.receipt_service.list(limit=limit)
            if not receipts:
                return "No recent actions."
                
            buffer = ["--- RECENT ACTIONS (Short-term Memory) ---"]
            # Reverse loop to show oldest to newest? No, usually newest at top or bottom?
            # list() usually returns newest first. LLM reads top-down. 
            # Let's show newest first at the top of the section? 
            # actually chronological (oldest -> newest) is better for causal reasoning.
            # So we reverse the list from service.
            
            for r in reversed(receipts):
                 status_icon = "✅" if r.status == ReceiptStatus.SUCCESS.value else "❌" if r.status == ReceiptStatus.FAILURE.value else "⏳"
                 # Truncate inputs/outputs for token sanity
                 inputs_str = str(r.inputs)
                 if len(inputs_str) > 200: inputs_str = inputs_str[:200] + "..."
                 
                 outputs_str = str(r.outputs)
                 if len(outputs_str) > 200: outputs_str = outputs_str[:200] + "..."
                 
                 buffer.append(f"{status_icon} [{r.timestamp[11:19]}] {r.action_name} ({r.action_type})")
                 buffer.append(f"    In: {inputs_str}")
                 if r.outputs:
                    buffer.append(f"    Out: {outputs_str}")
            
            return "\n".join(buffer)
        except Exception as e:
            return f"Error fetching receipts: {e}"

    def get_context_string(self, channel: str = None) -> str:
        """Formats context for the LLM.

        Accepts optional channel to filter history by source channel.
        """
        buffer = []

        # 1. File Context
        if self.items:
            buffer.append("--- BEGIN FILE CONTEXT ---")
            for item_id, item in self.items.items():
                buffer.append(f"\n[FILE: {item_id}]\n{item.content}")
            buffer.append("\n--- END FILE CONTEXT ---")

        # 2. Receipt Context
        receipts_str = self.get_recent_receipts(limit=15)
        buffer.append(f"\n{receipts_str}")

        # 3. Chat History (filtered by channel when specified)
        history_str = self.get_history_string(limit=50, channel=channel)
        buffer.append(f"\n{history_str}")
        
        if not buffer:
            return ""
            
        return "\n".join(buffer)

    def search_workspace(self, query: str, limit: int = 10) -> str:
        """Searches for a string in the workspace."""
        results = []
        count = 0
        
        # Receipt for search
        receipt = create_receipt(ActionType.FILE_OP, "search_workspace", {"query": query}, tier=CognitionTier.DETERMINISTIC, quest_id=self._current_quest_id)
        self.receipt_service.create(receipt)
        start_time = time.time()
        
        try:
            for root, _, files in os.walk(self.data_dir):
                for file in files:
                    if file.startswith(".") or file.endswith((".pyc", ".db", ".sqlite", ".git", ".json")):
                        continue
                        
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.data_dir)
                    
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            
                        if query.lower() in content.lower():
                            # Extract snippet
                            idx = content.lower().find(query.lower())
                            start = max(0, idx - 50)
                            end = min(len(content), idx + len(query) + 50)
                            snippet = content[start:end].replace("\n", " ")
                            results.append(f"[MATCH] {rel_path}: ...{snippet}...")
                            count += 1
                            if count >= limit:
                                break
                    except OSError as exc:
                        logger.debug(
                            "ContextEnvironment skipped unreadable file %s during workspace search: %s",
                            full_path,
                            exc,
                        )
                        continue
                if count >= limit:
                    break
            
            output = "\n".join(results) if results else "No matches found."
            
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.complete({"matches": count}, duration))
            return output
            
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.fail(str(e), duration))
            return f"Search failed: {e}"

    def get_file_outline(self, file_path: str) -> str:
        """Generates a high-level outline of a file (AST for Python)."""
         # Resolve path
        if not os.path.isabs(file_path):
            full_path = os.path.join(self.data_dir, file_path)
        else:
            full_path = file_path
            
        if not self._is_safe_path(full_path) or not os.path.exists(full_path):
             return "File access error."

        receipt = create_receipt(ActionType.FILE_OP, "read_outline", {"path": file_path}, tier=CognitionTier.DETERMINISTIC, quest_id=self._current_quest_id)
        self.receipt_service.create(receipt)
        start_time = time.time()

        try:
            output = []
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            if file_path.endswith(".py"):
                try:
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ClassDef):
                            output.append(f"class {node.name}")
                            # Methods
                            for item in node.body:
                                if isinstance(item, ast.FunctionDef):
                                    doc = ast.get_docstring(item)
                                    doc_stub = f" # {doc.splitlines()[0]}" if doc else ""
                                    output.append(f"  def {item.name}(...){doc_stub}")
                        elif isinstance(node, ast.FunctionDef):
                            # Top level functions
                             doc = ast.get_docstring(node)
                             doc_stub = f" # {doc.splitlines()[0]}" if doc else ""
                             output.append(f"def {node.name}(...){doc_stub}")
                except SyntaxError as exc:
                    logger.debug(
                        "ContextEnvironment AST parse failed for %s: %s",
                        full_path,
                        exc,
                    )
                    output.append("(AST Parse Failed) Showing first 10 lines:")
                    output.append("\n".join(content.splitlines()[:10]))
            else:
                 output.append("(Text File) Showing first 10 lines:")
                 output.append("\n".join(content.splitlines()[:10]))

            result = "\n".join(output)
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.complete({"size_bytes": len(result)}, duration))
            return result

        except Exception as e:
             duration = int((time.time() - start_time) * 1000)
             self.receipt_service.update(receipt.fail(str(e), duration))
             return f"Outline failed: {e}"

    def get_workspace_diff(self, staged: bool = False) -> str:
        """Returns git status and diff summary."""
        receipt = create_receipt(ActionType.TOOL_CALL, "read_diff", {"staged": staged}, tier=CognitionTier.DETERMINISTIC, quest_id=self._current_quest_id)
        self.receipt_service.create(receipt)
        start_time = time.time()
        
        try:
            # 1. Get Status
            status_cmd = ["git", "status", "--porcelain"]
            if staged:
                 status_cmd = ["git", "diff", "--name-status", "--cached"]
                 
            status_out = subprocess.check_output(status_cmd, cwd=self.data_dir, text=True, stderr=subprocess.STDOUT)
            
            # 2. Get Diff (Truncated)
            diff_cmd = ["git", "diff", "HEAD"]
            if staged:
                diff_cmd = ["git", "diff", "--cached"]
                
            # Limit diff output to prevent massive context context
            # We can't easily limit lines via git command args without external tools like 'head', 
            # so we capture output and truncate python-side.
            diff_out = subprocess.check_output(diff_cmd, cwd=self.data_dir, text=True, stderr=subprocess.STDOUT)
            
            if len(diff_out) > 5000:
                diff_out = diff_out[:5000] + "\n... [DIFF TRUNCATED]"
                
            output = f"--- GIT STATUS ---\n{status_out}\n\n--- GIT DIFF (HEAD) ---\n{diff_out}"
            
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.complete({"size_bytes": len(output)}, duration))
            return output
            
        except subprocess.CalledProcessError as e:
            err = f"Git Error: {e.output}"
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.fail(err, duration))
            return err
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.fail(str(e), duration))
            return f"Diff failed: {e}"

    def list_workspace(self, dir_path: str = ".") -> str:
        """Safely lists files in a directory."""
        # Resolve path
        if not os.path.isabs(dir_path):
             full_path = os.path.join(self.data_dir, dir_path)
        else:
             full_path = dir_path
        
        # Security Check
        if not self._is_safe_path(full_path):
             return "Access Denied: Path Traversal Detected"
             
        if not os.path.exists(full_path):
             return "Directory not found."
             
        receipt = create_receipt(ActionType.FILE_OP, "read_dir", {"path": dir_path}, tier=CognitionTier.DETERMINISTIC, quest_id=self._current_quest_id)
        self.receipt_service.create(receipt)
        start_time = time.time()
        
        try:
            items = os.listdir(full_path)
            # Filter hidden/system
            items = [i for i in items if not i.startswith(".") and not i.endswith((".pyc", "__pycache__"))]
            items.sort()
            
            output = []
            for item in items:
                item_path = os.path.join(full_path, item)
                if os.path.isdir(item_path):
                    output.append(f"[DIR] {item}/")
                else:
                    output.append(f"[FILE] {item}")
            
            result = "\n".join(output)
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.complete({"count": len(items)}, duration))
            return result
            
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            self.receipt_service.update(receipt.fail(str(e), duration))
            return f"List failed: {e}"
