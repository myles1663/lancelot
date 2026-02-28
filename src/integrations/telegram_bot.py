"""
Telegram Bot Integration for Lancelot
--------------------------------------
Long-polling bot that receives messages via Telegram Bot API
and routes them through the Lancelot orchestrator.

Supports text messages and voice notes (Fix Pack V1 PR7).
Uses only `requests` (no extra dependencies beyond voice_processor).
"""

import json
import os
import re
import time
import threading
import logging
import unicodedata
import requests

logger = logging.getLogger("lancelot.telegram_bot")

# Telegram Bot API base
TG_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    """
    Polls Telegram for new messages and replies via the orchestrator.
    Supports text messages and voice notes (STT → LLM → TTS → voice reply).
    """

    # V33: Persist offset to avoid re-processing messages on restart
    # Stored in chat/ subdir to avoid Librarian file watcher auto-moving it
    _OFFSET_FILE = os.path.join(os.getenv("LANCELOT_DATA_DIR", "/home/lancelot/data"), "chat", "telegram_offset.txt")

    def __init__(self, orchestrator=None, voice_processor=None):
        self.token = os.getenv("LANCELOT_TELEGRAM_TOKEN", "")
        self.chat_id = os.getenv("LANCELOT_TELEGRAM_CHAT_ID", "")
        self.orchestrator = orchestrator
        self.voice_processor = voice_processor
        self.running = False
        self._offset = self._load_offset()  # V33: Persist across restarts
        self._receipt_service = None  # Set externally if available

        if not self.token:
            logger.warning("TelegramBot: No LANCELOT_TELEGRAM_TOKEN set.")
        if not self.chat_id:
            logger.warning("TelegramBot: No LANCELOT_TELEGRAM_CHAT_ID set.")

        if self._offset:
            logger.info("TelegramBot: Restored offset=%d from disk", self._offset)

    @classmethod
    def _load_offset(cls) -> int:
        """Load persisted offset from disk."""
        try:
            with open(cls._OFFSET_FILE, "r") as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return 0

    def _save_offset(self) -> None:
        """Persist current offset to disk."""
        try:
            os.makedirs(os.path.dirname(self._OFFSET_FILE), exist_ok=True)
            with open(self._OFFSET_FILE, "w") as f:
                f.write(str(self._offset))
        except Exception:
            pass  # Non-critical — worst case we re-process on restart

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_polling(self):
        """Starts the background polling thread."""
        if self.running or not self.token:
            return
        self.running = True
        threading.Thread(target=self._poll_loop, daemon=True).start()
        logger.info(f"TelegramBot: Polling started (chat_id={self.chat_id})")

    def stop_polling(self):
        self.running = False
        logger.info("TelegramBot: Polling stopped.")

    @staticmethod
    def _display_width(text: str) -> int:
        """Calculate the visual display width of a string in monospace font.

        Emojis and wide CJK characters occupy 2 columns in monospace.
        Regular ASCII/Latin characters occupy 1 column.
        """
        width = 0
        i = 0
        chars = list(text)
        while i < len(chars):
            c = chars[i]
            cp = ord(c)

            # Variation selectors and zero-width joiners: 0 width
            if cp in (0xFE0E, 0xFE0F, 0x200D, 0x200B, 0x200C, 0x200D):
                i += 1
                continue

            # Combining marks: 0 width
            cat = unicodedata.category(c)
            if cat.startswith("M"):
                i += 1
                continue

            # Emoji ranges: 2 width
            if (0x1F300 <= cp <= 0x1FAF8 or  # Misc symbols, emoticons, transport, etc.
                0x2600 <= cp <= 0x27BF or     # Misc symbols, dingbats
                0x2700 <= cp <= 0x27BF or     # Dingbats
                0xFE00 <= cp <= 0xFE0F or     # Variation selectors
                0x1F900 <= cp <= 0x1F9FF or   # Supplemental symbols
                0x2300 <= cp <= 0x23FF or     # Misc technical (hourglass etc.)
                0x2B50 <= cp <= 0x2B55):      # Stars, circles
                width += 2
                i += 1
                continue

            # East Asian Width: W or F = 2 columns
            eaw = unicodedata.east_asian_width(c)
            if eaw in ("W", "F"):
                width += 2
            else:
                width += 1
            i += 1
        return width

    @staticmethod
    def _pad_to_width(text: str, target_width: int) -> str:
        """Pad a string with spaces to reach a target display width."""
        current = TelegramBot._display_width(text)
        pad = max(0, target_width - current)
        return text + " " * pad

    @staticmethod
    def _strip_emoji(text: str) -> str:
        """Strip emoji characters from text, keeping ASCII and basic symbols.

        Used to remove emojis from monospace table cells since Telegram renders
        emojis at inconsistent widths in monospace code blocks, breaking alignment.
        """
        result = []
        for c in text:
            cp = ord(c)
            # Skip variation selectors, ZWJ, zero-width chars
            if cp in (0xFE0E, 0xFE0F, 0x200D, 0x200B, 0x200C):
                continue
            # Skip emoji ranges
            if (0x1F300 <= cp <= 0x1FAF8 or
                0x2600 <= cp <= 0x27BF or
                0x2700 <= cp <= 0x27BF or
                0x1F900 <= cp <= 0x1F9FF or
                0x2300 <= cp <= 0x23FF or
                0x2B50 <= cp <= 0x2B55):
                continue
            # Skip combining marks
            if unicodedata.category(c).startswith("M"):
                continue
            result.append(c)
        # Clean up double spaces left by emoji removal
        cleaned = "".join(result)
        while "  " in cleaned:
            cleaned = cleaned.replace("  ", " ")
        return cleaned.strip()

    @staticmethod
    def _is_separator_row(line: str) -> bool:
        """Check if a line is a markdown table separator (|---|---|)."""
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            return False
        inner = stripped.replace("|", "").replace("-", "").replace(":", "").strip()
        return inner == ""

    # Max monospace line width for Telegram mobile
    _MAX_TABLE_WIDTH = 45

    @staticmethod
    def _table_to_monospace(text: str) -> str:
        """Convert markdown tables to monospace code blocks for Telegram.

        Uses ``` pre ``` blocks so columns stay aligned on mobile.
        Strips emojis and markdown from cell values.
        Caps total width to fit phone screens (~36 chars).
        If too many columns, shows only columns that fit.
        """
        lines = text.split("\n")
        result = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r"^\s*\|.+\|\s*$", line) and not TelegramBot._is_separator_row(line):
                # Parse header cells — strip emoji + bold for clean monospace
                raw_headers = [c.strip().replace("**", "") for c in line.strip().strip("|").split("|")]
                raw_headers = [h for h in raw_headers if h]
                headers = [TelegramBot._strip_emoji(h) for h in raw_headers]

                # Skip separator row
                if i + 1 < len(lines) and TelegramBot._is_separator_row(lines[i + 1]):
                    i += 2
                else:
                    i += 1

                # Collect all data rows — strip emoji from cells
                all_rows = [headers]
                while i < len(lines) and re.match(r"^\s*\|.+\|\s*$", lines[i]):
                    if TelegramBot._is_separator_row(lines[i]):
                        i += 1
                        continue
                    raw_cells = [c.strip().replace("**", "") for c in lines[i].strip().strip("|").split("|")]
                    cells = [TelegramBot._strip_emoji(c) for c in raw_cells]
                    while len(cells) < len(headers):
                        cells.append("")
                    all_rows.append(cells[:len(headers)])
                    i += 1

                # Calculate data widths (excluding header) and header widths separately
                data_widths = [0] * len(headers)
                header_widths = [len(h) for h in headers]
                for row in all_rows[1:]:  # Skip header row
                    for j, cell in enumerate(row):
                        if j < len(data_widths):
                            data_widths[j] = max(data_widths[j], len(cell))

                # Column width = max(data_width, header capped at data+2)
                # This prevents long headers from bloating narrow columns
                col_widths = []
                for j in range(len(headers)):
                    dw = data_widths[j] if j < len(data_widths) else 0
                    hw = header_widths[j] if j < len(header_widths) else 0
                    col_widths.append(max(dw, min(hw, dw + 3)))

                # Fit within max width: truncate wide columns, drop if needed
                max_w = TelegramBot._MAX_TABLE_WIDTH
                col_widths, n_cols = TelegramBot._fit_columns(col_widths, max_w)

                # Truncate all rows to selected columns
                all_rows = [row[:n_cols] for row in all_rows]

                # Truncate cell values that exceed their column width
                for row in all_rows:
                    for j, cell in enumerate(row):
                        if j < len(col_widths) and len(cell) > col_widths[j]:
                            row[j] = cell[:col_widths[j] - 1] + "…" if col_widths[j] > 1 else cell[:1]

                # Build aligned table
                table_lines = []
                for row_idx, row in enumerate(all_rows):
                    padded = [cell.ljust(col_widths[j]) for j, cell in enumerate(row)]
                    table_lines.append(" ".join(padded))
                    if row_idx == 0:
                        table_lines.append(" ".join("-" * w for w in col_widths))

                result.append("```\n" + "\n".join(table_lines) + "\n```")
            else:
                result.append(line)
                i += 1
        return "\n".join(result)

    @staticmethod
    def _fit_columns(col_widths: list, max_total: int) -> tuple:
        """Fit columns within max_total width. Returns (adjusted_widths, n_cols).

        Strategy: include columns left-to-right. If total exceeds max,
        first shrink the widest columns, then drop rightmost columns.
        """
        n = len(col_widths)
        if n == 0:
            return [], 0

        # Start with all columns; total = sum(widths) + (n-1) separators
        total = sum(col_widths) + (n - 1)
        if total <= max_total:
            return col_widths, n

        # Phase 1: cap each column to max 12 chars
        capped = [min(w, 12) for w in col_widths]
        total = sum(capped) + (n - 1)
        if total <= max_total:
            return capped, n

        # Phase 2: cap each column to max 8 chars
        capped = [min(w, 8) for w in capped]
        total = sum(capped) + (n - 1)
        if total <= max_total:
            return capped, n

        # Phase 3: drop rightmost columns until it fits
        for keep in range(n, 0, -1):
            subset = capped[:keep]
            total = sum(subset) + (keep - 1)
            if total <= max_total:
                return subset, keep

        # Absolute minimum: first column only, truncated
        return [max_total], 1

    @staticmethod
    def _sanitize_for_telegram(text: str) -> str:
        """Convert GitHub-flavored Markdown to Telegram Markdown v1 and strip
        internal tool scaffolding.

        Telegram Markdown v1 supports only: *bold*, _italic_, `code`,
        ```pre```, and [text](url). Headers, horizontal rules, tables, and
        **double-star bold** must be converted or stripped.
        """
        # --- Raw JSON block detection and removal ---
        # Prevents raw API responses (e.g. wttr.in JSON) from being sent to users.
        # Strips any block that looks like a JSON object/array > 500 chars.
        def _strip_json_blocks(t: str) -> str:
            # Remove JSON code blocks (```json ... ```)
            t = re.sub(r"```(?:json)?\s*\n\s*[\[{].*?```", "[data omitted]", t, flags=re.DOTALL)
            # Remove bare JSON objects/arrays > 500 chars
            cleaned_lines = []
            json_buf = []
            in_json = False
            brace_depth = 0
            for line in t.split("\n"):
                stripped = line.strip()
                if not in_json and stripped and stripped[0] in "{[":
                    in_json = True
                    json_buf = [line]
                    brace_depth = stripped.count("{") + stripped.count("[") - stripped.count("}") - stripped.count("]")
                    if brace_depth <= 0:
                        blob = "\n".join(json_buf)
                        if len(blob) > 500:
                            try:
                                json.loads(blob)
                                cleaned_lines.append("[data omitted]")
                            except (json.JSONDecodeError, ValueError):
                                cleaned_lines.append(blob)
                        else:
                            cleaned_lines.append(blob)
                        in_json = False
                        json_buf = []
                    continue
                if in_json:
                    json_buf.append(line)
                    brace_depth += stripped.count("{") + stripped.count("[") - stripped.count("}") - stripped.count("]")
                    if brace_depth <= 0:
                        blob = "\n".join(json_buf)
                        if len(blob) > 500:
                            try:
                                json.loads(blob)
                                cleaned_lines.append("[data omitted]")
                            except (json.JSONDecodeError, ValueError):
                                cleaned_lines.append(blob)
                        else:
                            cleaned_lines.append(blob)
                        in_json = False
                        json_buf = []
                    continue
                cleaned_lines.append(line)
            # If we were still in a JSON block at end
            if json_buf:
                blob = "\n".join(json_buf)
                if len(blob) > 500:
                    cleaned_lines.append("[data omitted]")
                else:
                    cleaned_lines.extend(json_buf)
            return "\n".join(cleaned_lines)

        text = _strip_json_blocks(text)

        # --- Tool scaffolding removal ---
        text = re.sub(r"\s*\(Tool:\s*\w+,?\s*Params:\s*[^)]*\)", "", text)
        text = re.sub(r",?\s*model=[\w.\-]+", "", text)
        text = re.sub(r",?\s*user_message=[^,\n)]+", "", text)
        text = re.sub(r"^Action:\s?.*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"```(?:Tool_Code|tool_code)?\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"^Tool_Code\s*\n.*?(?=\n\n|\Z)", "", text, flags=re.MULTILINE | re.DOTALL)
        text = re.sub(r"print\s*\([^)]*\)", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"^[A-Za-z0-9_/+=\-]{10,}\?[^\s)]*\)?\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\S*\?oc=\d+\)?\s*$", "", text, flags=re.MULTILINE)

        # --- Table conversion (before other markdown transforms) ---
        text = TelegramBot._table_to_monospace(text)

        # --- Markdown → Telegram conversion ---
        # Step 1: Convert headers FIRST (strip inner ** to avoid nested stars)
        # e.g. "### 🌡️ **Temperature**" → "*🌡️ Temperature*"
        def _header_to_bold(m):
            content = m.group(1).replace("**", "").replace("*", "")
            return f"*{content}*"
        text = re.sub(r"^#{1,6}\s+(.+)$", _header_to_bold, text, flags=re.MULTILINE)

        # Step 2: Convert remaining **bold** to *bold*
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)

        # Step 3: Convert horizontal rules (--- or ***) to blank line
        text = re.sub(r"^[\-\*_]{3,}\s*$", "", text, flags=re.MULTILINE)

        # Step 4: Convert bullet dashes to • for cleaner display
        # Only match "- " at line start (not "* " which is now bold)
        text = re.sub(r"^(\s*)-\s+", r"\1• ", text, flags=re.MULTILINE)

        # --- Safety: fix unbalanced stars that break Telegram Markdown ---
        # Count unescaped * outside of code blocks (``` ... ```)
        # If odd, Telegram will reject the entire message
        parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
        star_count = 0
        for i, part in enumerate(parts):
            if i % 2 == 0:  # Outside code blocks
                star_count += part.count("*")
        if star_count % 2 != 0:
            # Odd number of stars — strip all bold to be safe
            new_parts = []
            for i, part in enumerate(parts):
                if i % 2 == 0:  # Outside code blocks
                    new_parts.append(part.replace("*", ""))
                else:
                    new_parts.append(part)
            text = "".join(new_parts)

        # --- Cleanup ---
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        # --- Convert Markdown v1 → HTML for reliable Telegram rendering ---
        text = TelegramBot._markdown_to_html(text)
        return text

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """Convert Telegram Markdown v1 to HTML for more reliable rendering.

        HTML parse_mode is far more reliable than Markdown v1:
        - <pre> blocks render true monospace (perfect table alignment)
        - No issues with unbalanced *, _, or other markdown quirks
        - <b> for bold, <i> for italic
        """
        # Split by code blocks to handle them separately
        parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
        html_parts = []

        for idx, part in enumerate(parts):
            if idx % 2 == 1:
                # Code block — extract content, escape HTML, wrap in <pre>
                content = part
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip("\n")
                content = content.replace("&", "&amp;")
                content = content.replace("<", "&lt;")
                content = content.replace(">", "&gt;")
                html_parts.append(f"<pre>{content}</pre>")
            else:
                # Regular text — escape HTML, then convert markdown to tags
                part = part.replace("&", "&amp;")
                part = part.replace("<", "&lt;")
                part = part.replace(">", "&gt;")
                # Convert *bold* → <b>bold</b>
                part = re.sub(r"\*([^*]+)\*", r"<b>\1</b>", part)
                # Convert `inline code` → <code>code</code>
                part = re.sub(r"`([^`]+)`", r"<code>\1</code>", part)
                html_parts.append(part)

        return "".join(html_parts)

    @staticmethod
    def _chunk_by_lines(text: str, max_size: int = 4000) -> list:
        """V28: Split text into chunks at line boundaries.

        Avoids breaking markdown links mid-URL which causes orphaned
        URL fragments to appear at the top of the next Telegram message.
        Falls back to hard split only for single lines exceeding max_size.
        """
        lines = text.split("\n")
        chunks = []
        current_lines = []
        current_len = 0

        for line in lines:
            line_len = len(line) + 1  # +1 for newline
            if current_len + line_len > max_size and current_lines:
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_len = 0
            # Single line exceeds max_size — hard split as fallback
            if len(line) > max_size:
                for i in range(0, len(line), max_size):
                    chunks.append(line[i:i + max_size])
            else:
                current_lines.append(line)
                current_len += line_len

        if current_lines:
            chunks.append("\n".join(current_lines))

        return chunks if chunks else [text]

    def send_message_with_keyboard(self, text: str, keyboard: dict = None, chat_id: str = None):
        """Send message with optional InlineKeyboardMarkup. Returns message_id or None.

        Used by ActionCards to send interactive buttons and by ToolFlow
        progress bridge to send editable progress messages.
        """
        target = chat_id or self.chat_id
        if not self.token or not target:
            logger.warning("TelegramBot: Cannot send (token or chat_id missing).")
            return None

        url = TG_API.format(token=self.token, method="sendMessage")
        payload = {
            "chat_id": target,
            "text": text,
            "disable_web_page_preview": True,
        }

        if keyboard:
            payload["reply_markup"] = keyboard

        # Try Markdown first, fall back to stripped plain text
        for attempt in range(2):
            try:
                if attempt == 0:
                    payload["parse_mode"] = "HTML"
                else:
                    payload.pop("parse_mode", None)
                    # Strip HTML/markdown formatting for clean plain text
                    clean = re.sub(r"</?(?:b|i|pre|code)>", "", text)
                    clean = clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    payload["text"] = clean

                resp = requests.post(url, json=payload, timeout=15)
                if resp.ok:
                    result = resp.json().get("result", {})
                    message_id = result.get("message_id")
                    return message_id
                if attempt == 0:
                    logger.warning("TelegramBot: HTML send_with_keyboard failed, retrying plain")
            except Exception as e:
                logger.error("TelegramBot: send_message_with_keyboard error (attempt %d): %s", attempt + 1, e)
                if attempt == 0:
                    time.sleep(1)

        return None

    def edit_message(self, message_id: int, text: str, chat_id: str = None, keyboard: dict = None) -> bool:
        """Edit an existing message. Uses Telegram editMessageText API.

        Returns True on success, False otherwise.
        """
        target = chat_id or self.chat_id
        if not self.token or not target:
            logger.warning("TelegramBot: Cannot edit (token or chat_id missing).")
            return False

        url = TG_API.format(token=self.token, method="editMessageText")
        payload = {
            "chat_id": target,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }

        if keyboard is not None:
            payload["reply_markup"] = keyboard

        # Try Markdown first, fall back to stripped plain text
        for attempt in range(2):
            try:
                if attempt == 0:
                    payload["parse_mode"] = "HTML"
                else:
                    payload.pop("parse_mode", None)
                    # Strip HTML/markdown formatting for clean plain text
                    clean = re.sub(r"</?(?:b|i|pre|code)>", "", text)
                    clean = clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    payload["text"] = clean

                resp = requests.post(url, json=payload, timeout=15)
                if resp.ok:
                    return True
                # Telegram returns error if message content is unchanged — not a real error
                if resp.status_code == 400 and "message is not modified" in resp.text.lower():
                    return True
                if attempt == 0:
                    logger.warning("TelegramBot: HTML edit failed, retrying plain")
            except Exception as e:
                logger.error("TelegramBot: edit_message error (attempt %d): %s", attempt + 1, e)
                if attempt == 0:
                    time.sleep(1)

        return False

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        """Answer a callback_query (required by Telegram API to stop the loading spinner).

        Returns True on success.
        """
        if not self.token:
            return False

        url = TG_API.format(token=self.token, method="answerCallbackQuery")
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]  # Telegram limit for callback answer

        try:
            resp = requests.post(url, json=payload, timeout=15)
            return resp.ok
        except Exception as e:
            logger.error("TelegramBot: answer_callback_query error: %s", e)
            return False

    def send_message(self, text: str, chat_id: str = None):
        """Sends a message to the configured chat.

        V15b: Retries failed chunks once and logs which chunk failed for
        debugging. Previously, a failed chunk was silently skipped, causing
        the user to receive an incomplete response with no indication.
        """
        target = chat_id or self.chat_id
        if not self.token or not target:
            logger.warning("TelegramBot: Cannot send (token or chat_id missing).")
            return

        # V33: Guard against raw JSON being sent as message text
        stripped = text.strip()
        if len(stripped) > 500 and stripped[:1] in ("{", "["):
            try:
                json.loads(stripped)
                logger.warning("TelegramBot: BLOCKED raw JSON message (%d chars). First 100: %s",
                               len(stripped), stripped[:100])
                return  # Silently drop raw JSON — it's never intended for the user
            except (json.JSONDecodeError, ValueError):
                pass  # Not valid JSON, send normally

        # V33: Debug trace — log every outgoing message (first 200 chars + length)
        logger.info("TelegramBot: send_message called — len=%d first200=%s",
                     len(text), repr(text[:200]))
        # TEMP: dump full message to file for table alignment debugging
        try:
            _dbg = os.path.join(os.getenv("LANCELOT_DATA_DIR", "/home/lancelot/data"), "chat", "last_msg_debug.txt")
            with open(_dbg, "w") as _f:
                _f.write(text)
        except Exception:
            pass

        url = TG_API.format(token=self.token, method="sendMessage")

        # Telegram limit is 4096 chars per message; chunk if needed
        # V28: Split at line boundaries to avoid breaking markdown links mid-URL
        chunks = TelegramBot._chunk_by_lines(text, max_size=4000)
        total_chunks = len(chunks)
        failed_chunks = []

        for idx, chunk in enumerate(chunks):
            sent = False
            for attempt in range(2):  # V15b: retry once on failure
                try:
                    if attempt == 0:
                        parse_mode = "HTML"
                        send_chunk = chunk
                    else:
                        # V34: On retry, strip ALL HTML/markdown formatting so text is clean
                        parse_mode = None
                        send_chunk = re.sub(r"</?(?:b|i|pre|code)>", "", chunk)  # Strip HTML tags
                        send_chunk = re.sub(r"```\n?", "", send_chunk)  # Remove code fences (if any)
                        send_chunk = send_chunk.replace("*", "")  # Remove any remaining bold
                        send_chunk = send_chunk.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    payload = {
                        "chat_id": target,
                        "text": send_chunk,
                        "disable_web_page_preview": True,
                    }
                    if parse_mode:
                        payload["parse_mode"] = parse_mode
                    resp = requests.post(url, json=payload, timeout=15)
                    if resp.ok:
                        sent = True
                        break
                    # First attempt with Markdown failed — retry without it
                    if attempt == 0:
                        logger.warning("TelegramBot: Markdown send failed for chunk %d/%d, retrying plain. Error: %s",
                                       idx + 1, total_chunks, resp.text[:200])
                except Exception as e:
                    logger.error("TelegramBot: Send error chunk %d/%d (attempt %d): %s", idx + 1, total_chunks, attempt + 1, e)
                    if attempt == 0:
                        time.sleep(1)  # Brief pause before retry

            if not sent:
                failed_chunks.append(idx + 1)
                logger.error("TelegramBot: Chunk %d/%d permanently failed", idx + 1, total_chunks)

        if failed_chunks and total_chunks > 1:
            logger.error("TelegramBot: %d/%d chunks failed to send: %s", len(failed_chunks), total_chunks, failed_chunks)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self):
        """Long-polling loop using getUpdates."""
        logger.info("TelegramBot: _poll_loop thread started (running=%s)", self.running)
        while self.running:
            try:
                url = TG_API.format(token=self.token, method="getUpdates")
                resp = requests.post(url, json={
                    "offset": self._offset,
                    "timeout": 30,  # long-poll 30s
                    "allowed_updates": ["message", "callback_query"],
                }, timeout=40)

                if not resp.ok:
                    logger.error("TelegramBot: Poll HTTP %s: %s", resp.status_code, resp.text[:200])
                    time.sleep(5)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    logger.error("TelegramBot: API error: %s", data)
                    time.sleep(5)
                    continue

                updates = data.get("result", [])
                if updates:
                    logger.info("TelegramBot: Received %d update(s), offset=%s", len(updates), self._offset)
                for update in updates:
                    self._handle_update(update)
                # V33: Persist offset after processing batch so restarts don't re-process
                if updates:
                    self._save_offset()

            except requests.exceptions.Timeout:
                # Normal for long-polling — just loop again
                continue
            except Exception as e:
                logger.error("TelegramBot: Poll error: %s", e)
                time.sleep(5)

    def send_document(self, file_bytes: bytes, filename: str, chat_id: str = None, caption: str = None):
        """Sends a document/file to the configured chat."""
        # V33: Trace all document sends for debugging JSON injection
        import traceback
        logger.info("TelegramBot: send_document called — file=%s size=%d caption=%s caller=%s",
                     filename, len(file_bytes), (caption or "")[:50],
                     "".join(traceback.format_stack()[-3:-1]).strip()[:200])

        # V33: Block raw JSON files from being sent as documents
        if filename.endswith(".json") and len(file_bytes) > 1000:
            logger.warning("TelegramBot: BLOCKED JSON document send — file=%s size=%d", filename, len(file_bytes))
            return False

        target = chat_id or self.chat_id
        if not self.token or not target:
            logger.warning("TelegramBot: Cannot send document (token or chat_id missing).")
            return False

        url = TG_API.format(token=self.token, method="sendDocument")
        try:
            data = {"chat_id": target}
            if caption:
                data["caption"] = caption[:1024]  # Telegram caption limit
            resp = requests.post(
                url,
                data=data,
                files={"document": (filename, file_bytes, "application/octet-stream")},
                timeout=60,
            )
            if not resp.ok:
                logger.error("TelegramBot: Send document failed: %s", resp.text[:200])
                return False
            logger.info("TelegramBot: Sent document '%s' (%d bytes)", filename, len(file_bytes))
            return True
        except Exception as e:
            logger.error("TelegramBot: Send document error: %s", e)
            return False

    def send_voice(self, audio_bytes: bytes, chat_id: str = None):
        """Sends a voice note (OGG/OPUS) to the configured chat."""
        target = chat_id or self.chat_id
        if not self.token or not target:
            logger.warning("TelegramBot: Cannot send voice (token or chat_id missing).")
            return

        url = TG_API.format(token=self.token, method="sendVoice")
        try:
            resp = requests.post(
                url,
                data={"chat_id": target},
                files={"voice": ("reply.ogg", audio_bytes, "audio/ogg")},
                timeout=30,
            )
            if not resp.ok:
                logger.error(f"TelegramBot: Send voice failed: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"TelegramBot: Send voice error: {e}")

    def _download_file(self, file_id: str) -> bytes:
        """Download a file from Telegram by file_id."""
        # Step 1: Get file path
        url = TG_API.format(token=self.token, method="getFile")
        resp = requests.get(url, params={"file_id": file_id}, timeout=15)
        if not resp.ok:
            raise RuntimeError(f"getFile failed: {resp.text[:200]}")

        file_path = resp.json().get("result", {}).get("file_path", "")
        if not file_path:
            raise RuntimeError("No file_path in getFile response")

        # Step 2: Download file content
        download_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        dl_resp = requests.get(download_url, timeout=30)
        if not dl_resp.ok:
            raise RuntimeError(f"File download failed: {dl_resp.status_code}")

        return dl_resp.content

    def _handle_update(self, update: dict):
        """Processes a single Telegram update.

        V15b: Offset is now incremented AFTER processing succeeds (or is
        deliberately skipped for non-message updates). This prevents permanent
        message loss if orchestrator.chat() crashes mid-processing.
        """
        update_id = update.get("update_id", 0)

        # V32: Handle callback_query updates (ActionCard button clicks)
        callback_query = update.get("callback_query")
        if callback_query:
            self._handle_callback_query(callback_query)
            self._offset = update_id + 1
            return

        msg = update.get("message")
        if not msg:
            # Non-message updates (edited_message, etc.) — ack and skip
            self._offset = update_id + 1
            return

        sender_chat_id = str(msg.get("chat", {}).get("id", ""))
        sender_name = msg.get("from", {}).get("first_name", "User")

        # Only respond to the configured chat (security)
        if self.chat_id and sender_chat_id != self.chat_id:
            logger.warning(f"TelegramBot: Ignoring message from unauthorized chat {sender_chat_id}")
            self._offset = update_id + 1  # Ack ignored messages
            return

        # Check for voice note / audio
        voice = msg.get("voice") or msg.get("audio")
        if voice:
            self._handle_voice(voice, sender_chat_id, sender_name)
            self._offset = update_id + 1
            return

        # V14: Check for photo messages
        photo = msg.get("photo")
        if photo:
            caption = msg.get("caption", "What's in this image?")
            largest = photo[-1]  # Highest resolution
            self._handle_photo(largest["file_id"], caption, sender_chat_id, sender_name)
            self._offset = update_id + 1
            return

        # V14: Check for document messages
        document = msg.get("document")
        if document:
            caption = msg.get("caption", "Please analyze this document.")
            self._handle_document(document, caption, sender_chat_id, sender_name)
            self._offset = update_id + 1
            return

        text = msg.get("text", "")
        if not text:
            self._offset = update_id + 1
            return

        logger.info(f"TelegramBot: [{sender_name}] {text[:50]}...")

        if not self.orchestrator:
            self.send_message("Lancelot orchestrator is not available.", sender_chat_id)
            self._offset = update_id + 1
            return

        try:
            response = self.orchestrator.chat(text, channel="telegram")
            if response:
                # V15: Skip sending if telegram_send already delivered this response
                if not getattr(self.orchestrator, '_telegram_already_sent', False):
                    response = self._sanitize_for_telegram(response)
                    self.send_message(response, sender_chat_id)
                else:
                    logger.info("TelegramBot: Response already sent via telegram_send — skipping duplicate")
                self.orchestrator._telegram_already_sent = False  # Reset for next message
            # V15b: Only ack after successful processing
            self._offset = update_id + 1
        except Exception as e:
            logger.error(f"TelegramBot: Orchestrator error: {e}")
            self.send_message(f"Error processing request: {e}", sender_chat_id)
            # V15b: Still ack on handled errors (user got an error message)
            self._offset = update_id + 1

    def _handle_voice(self, voice: dict, chat_id: str, sender_name: str):
        """Handle a voice note: STT → orchestrator → TTS → voice reply."""
        if not self.voice_processor:
            self.send_message(
                "Voice notes are not enabled. Please send a text message.",
                chat_id,
            )
            return

        file_id = voice.get("file_id", "")
        mime_type = voice.get("mime_type", "audio/ogg")
        duration = voice.get("duration", 0)

        logger.info(
            "TelegramBot: Voice note from [%s] (duration=%ds, mime=%s)",
            sender_name, duration, mime_type,
        )

        try:
            # Step 1: Download voice file from Telegram
            audio_bytes = self._download_file(file_id)
            logger.info("TelegramBot: Downloaded voice file (%d bytes)", len(audio_bytes))

            # Step 2: STT — transcribe audio to text
            stt_result = self.voice_processor.process_voice_note(audio_bytes, mime_type)
            transcribed_text = stt_result.text

            if not transcribed_text:
                self.send_message(
                    "I couldn't understand the voice note. Please try again or send text.",
                    chat_id,
                )
                return

            # V15b: Detect stub mode placeholder — don't feed it into the orchestrator
            if transcribed_text.startswith("[") and "not configured" in transcribed_text:
                logger.warning("TelegramBot: STT returned stub placeholder — voice not configured")
                self.send_message(
                    "Voice processing is not currently configured. Please send a text message instead.",
                    chat_id,
                )
                return

            logger.info(
                "TelegramBot: STT result: '%s' (confidence=%.2f)",
                transcribed_text[:80], stt_result.confidence,
            )

            # Step 3: Route through orchestrator
            if not self.orchestrator:
                self.send_message(
                    f"Transcribed: {transcribed_text}\n\n(Orchestrator not available)",
                    chat_id,
                )
                return

            response = self.orchestrator.chat(transcribed_text, channel="telegram")
            if not response:
                return

            # Step 4: TTS — synthesize response as voice
            response = self._sanitize_for_telegram(response)
            sent = False
            if self.voice_processor.available:
                try:
                    audio_reply = self.voice_processor.synthesize_reply(response)
                    if audio_reply:
                        self.send_voice(audio_reply, chat_id)
                        # Also send text version for accessibility
                        self.send_message(response, chat_id)
                        sent = True
                except Exception as tts_err:
                    logger.warning("TelegramBot: TTS failed, falling back to text: %s", tts_err)

            # Fallback: send text response (only if not already sent)
            if not sent:
                self.send_message(response, chat_id)

        except Exception as e:
            logger.error("TelegramBot: Voice processing error: %s", e)
            self.send_message(
                f"Error processing voice note: {e}",
                chat_id,
            )

    def _handle_photo(self, file_id: str, caption: str, chat_id: str, sender_name: str):
        """V14: Handle a photo — download → send to Gemini vision → respond."""
        logger.info("TelegramBot: Photo from [%s] caption='%s'", sender_name, caption[:50])

        if not self.orchestrator:
            self.send_message("Lancelot orchestrator is not available.", chat_id)
            return

        try:
            image_bytes = self._download_file(file_id)
            logger.info("TelegramBot: Downloaded photo (%d bytes)", len(image_bytes))

            # V15b: Guard against empty downloads
            if not image_bytes:
                self.send_message("Failed to download the photo. Please try sending it again.", chat_id)
                return

            from orchestrator import ChatAttachment
            attachment = ChatAttachment(
                filename="telegram_photo.jpg",
                mime_type="image/jpeg",
                data=image_bytes,
            )

            response = self.orchestrator.chat(caption, attachments=[attachment], channel="telegram")
            if response:
                response = self._sanitize_for_telegram(response)
                self.send_message(response, chat_id)

        except Exception as e:
            logger.error("TelegramBot: Photo processing error: %s", e)
            self.send_message(f"Error processing photo: {e}", chat_id)

    def _handle_document(self, document: dict, caption: str, chat_id: str, sender_name: str):
        """V14: Handle a document — download → read/analyze → respond."""
        file_id = document.get("file_id", "")
        file_name = document.get("file_name", "unknown")
        mime_type = document.get("mime_type", "application/octet-stream")

        logger.info("TelegramBot: Document from [%s]: %s (%s)", sender_name, file_name, mime_type)

        if not self.orchestrator:
            self.send_message("Lancelot orchestrator is not available.", chat_id)
            return

        try:
            file_bytes = self._download_file(file_id)
            logger.info("TelegramBot: Downloaded document (%d bytes)", len(file_bytes))

            # V15b: Guard against empty downloads
            if not file_bytes:
                self.send_message("Failed to download the document. Please try sending it again.", chat_id)
                return

            from orchestrator import ChatAttachment
            attachment = ChatAttachment(
                filename=file_name,
                mime_type=mime_type,
                data=file_bytes,
            )

            response = self.orchestrator.chat(caption, attachments=[attachment], channel="telegram")
            if response:
                response = self._sanitize_for_telegram(response)
                self.send_message(response, chat_id)

        except Exception as e:
            logger.error("TelegramBot: Document processing error: %s", e)
            self.send_message(f"Error processing document: {e}", chat_id)

    # ------------------------------------------------------------------
    # V32: ActionCard callback handling
    # ------------------------------------------------------------------

    def _handle_callback_query(self, callback_query: dict):
        """Handle inline keyboard button clicks from ActionCards.

        1. Parse callback_data: "ac:{card_id_prefix}:{button_id}"
        2. Security: verify chat_id matches LANCELOT_TELEGRAM_CHAT_ID
        3. Route to ActionCardResolver.resolve()
        4. answer_callback_query() to acknowledge
        5. edit_message() to remove keyboard and show resolution
        """
        query_id = callback_query.get("id", "")
        data = callback_query.get("data", "")
        sender_chat_id = str(
            callback_query.get("message", {}).get("chat", {}).get("id", "")
        )
        message_id = callback_query.get("message", {}).get("message_id")

        # Security gate: only accept callbacks from the configured chat
        if self.chat_id and sender_chat_id != self.chat_id:
            logger.warning(
                "TelegramBot: Ignoring callback from unauthorized chat %s",
                sender_chat_id,
            )
            self.answer_callback_query(query_id, "Unauthorized")
            return

        # Parse callback_data format: ac:{short_id}:{button_id}
        if not data.startswith("ac:"):
            logger.debug("TelegramBot: Ignoring non-ActionCard callback: %s", data)
            self.answer_callback_query(query_id)
            return

        parts = data.split(":", 2)
        if len(parts) != 3:
            logger.warning("TelegramBot: Malformed callback_data: %s", data)
            self.answer_callback_query(query_id, "Invalid callback data")
            return

        _, card_id_prefix, button_id = parts

        # Check if resolver is available (injected by gateway)
        resolver = getattr(self, "_action_card_resolver", None)
        if not resolver:
            logger.warning("TelegramBot: ActionCard resolver not available")
            self.answer_callback_query(query_id, "ActionCards not available")
            return

        # Resolve the action
        result = resolver.resolve(card_id_prefix, button_id, channel="telegram")
        status = result.get("status", "error")
        message = result.get("message", "")

        # Acknowledge the callback
        ack_text = f"{button_id.title()}: {message}" if message else button_id.title()
        self.answer_callback_query(query_id, ack_text[:200])

        # Edit the original message to remove keyboard and show resolution
        if message_id:
            original_text = callback_query.get("message", {}).get("text", "")
            if status in ("approved", "denied"):
                resolution_text = f"{original_text}\n\n[{status.upper()}] {message}"
            else:
                resolution_text = f"{original_text}\n\n[{status.upper()}] {message}"

            # Remove the inline keyboard by passing an empty keyboard
            self.edit_message(
                message_id,
                resolution_text,
                chat_id=sender_chat_id,
                keyboard={"inline_keyboard": []},
            )

        logger.info(
            "TelegramBot: ActionCard callback resolved: card=%s button=%s status=%s",
            card_id_prefix, button_id, status,
        )

    # ------------------------------------------------------------------
    # V32: Event-driven ActionCard presentation
    # ------------------------------------------------------------------

    async def _on_actioncard_event(self, event):
        """Handle actioncard_presented events — send inline keyboard to chat.

        Called by EventBus when an ActionCard is created. Sends the card
        as a Telegram message with inline keyboard buttons, then records
        the message_id in the ActionCardStore for later editing.
        """
        payload = event.payload
        card_id = payload.get("card_id", "")
        title = payload.get("title", "")
        description = payload.get("description", "")
        source_system = payload.get("source_system", "")

        # Reconstruct minimal card for keyboard generation
        try:
            from actioncard.models import ActionCard, ActionButton
            buttons_data = payload.get("buttons", [])
            buttons = [
                ActionButton(**b) if isinstance(b, dict) else b
                for b in buttons_data
            ]
            card = ActionCard(
                card_id=card_id,
                title=title,
                description=description,
                source_system=source_system,
                buttons=buttons,
            )
        except Exception as exc:
            logger.error("TelegramBot: Failed to reconstruct ActionCard from event: %s", exc)
            return

        # Build telegram text and keyboard
        text = card.to_telegram_text()
        keyboard = card.to_telegram_keyboard()

        # Send message with inline keyboard
        message_id = self.send_message_with_keyboard(text, keyboard=keyboard)

        if message_id:
            # Record message_id in store for cross-channel editing
            store = getattr(self, "_action_card_store", None)
            if store:
                try:
                    store.set_telegram_message_id(card_id, message_id)
                except Exception as exc:
                    logger.warning(
                        "TelegramBot: Failed to record message_id for card %s: %s",
                        card_id[:8], exc,
                    )
            logger.info(
                "TelegramBot: ActionCard sent: card=%s message_id=%s",
                card_id[:8], message_id,
            )
        else:
            logger.warning("TelegramBot: Failed to send ActionCard %s", card_id[:8])

    async def _on_actioncard_resolved_event(self, event):
        """Handle actioncard_resolved events — edit message if resolved from another channel.

        When an ActionCard is resolved via War Room or API (not Telegram),
        this updates the Telegram message to remove the keyboard and show
        the resolution status.
        """
        payload = event.payload
        channel = payload.get("channel", "")

        # If resolved from Telegram, _handle_callback_query already updated the message
        if channel == "telegram":
            return

        telegram_message_id = payload.get("telegram_message_id")
        if not telegram_message_id:
            return

        button_id = payload.get("button_id", "")
        result = payload.get("result", {})
        status = result.get("status", "resolved")
        message = result.get("message", "")

        # Edit the Telegram message to show resolution
        resolution_text = f"[{status.upper()}] {button_id.title()}"
        if message:
            resolution_text += f": {message}"

        self.edit_message(
            telegram_message_id,
            resolution_text,
            keyboard={"inline_keyboard": []},
        )

        logger.info(
            "TelegramBot: ActionCard cross-channel update: message=%s resolved via %s",
            telegram_message_id, channel,
        )
