"""Minimal HTML ↔ Tkinter Text rich editing (Word-like view, no raw tags)."""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser
from tkinter import Text, font as tkfont


class _HtmlToText(HTMLParser):
    """Parse a limited HTML subset into (text, tag_ranges)."""

    BLOCKS = {
        "p",
        "div",
        "section",
        "article",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "tr",
        "blockquote",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ranges: list[tuple[str, int, int]] = []  # tag, start, end (char indices)
        self._stack: list[tuple[str, int]] = []
        self._list_stack: list[str] = []  # ul / ol
        self._li_index = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("script", "style"):
            self._skip += 1
            return
        if self._skip:
            return
        if t == "br":
            self.parts.append("\n")
            return
        if t in ("ul", "ol"):
            self._list_stack.append(t)
            self._li_index = 0
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            return
        if t == "li":
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            self._li_index += 1
            if self._list_stack and self._list_stack[-1] == "ol":
                self.parts.append(f"{self._li_index}. ")
            else:
                self.parts.append("• ")
            self._stack.append(("li", self._len()))
            return
        if t in ("b", "strong"):
            self._stack.append(("bold", self._len()))
            return
        if t in ("i", "em"):
            self._stack.append(("italic", self._len()))
            return
        if t in ("u",):
            self._stack.append(("underline", self._len()))
            return
        if t in ("h1", "h2", "h3"):
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            self._stack.append((t, self._len()))
            return
        if t in self.BLOCKS:
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            return

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if t in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            return
        if t == "li":
            self._close_until("li")
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            return
        if t in ("b", "strong"):
            self._close_until("bold")
            return
        if t in ("i", "em"):
            self._close_until("italic")
            return
        if t == "u":
            self._close_until("underline")
            return
        if t in ("h1", "h2", "h3"):
            self._close_until(t)
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            return
        if t in self.BLOCKS:
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if not data:
            return
        # Collapse pure whitespace-ish noise between tags but keep single spaces
        text = data.replace("\r", "")
        if text.strip() == "" and "\n" in text:
            return
        self.parts.append(text)

    def _len(self) -> int:
        return sum(len(p) for p in self.parts)

    def _close_until(self, name: str):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == name:
                tag, start = self._stack.pop(i)
                end = self._len()
                if end > start:
                    self.ranges.append((tag, start, end))
                return

    def finish(self) -> tuple[str, list[tuple[str, int, int]]]:
        while self._stack:
            tag, start = self._stack.pop()
            end = self._len()
            if end > start:
                self.ranges.append((tag, start, end))
        text = "".join(self.parts)
        # Normalize 3+ newlines to 2
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip("\n"), self.ranges


def html_to_display(html: str) -> tuple[str, list[tuple[str, int, int]]]:
    raw = (html or "").strip()
    if not raw:
        return "", []
    # If it doesn't look like HTML, show as plain text
    if "<" not in raw or ">" not in raw:
        return raw, []
    parser = _HtmlToText()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        # fallback: strip tags crudely
        plain = re.sub(r"<[^>]+>", "", raw)
        plain = html_lib.unescape(plain)
        return plain.strip(), []
    return parser.finish()


def apply_html_to_text_widget(widget: Text, html: str) -> None:
    text, ranges = html_to_display(html)
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    if text:
        widget.insert("1.0", text)
    # Apply formatting ranges
    for tag, start, end in ranges:
        a = f"1.0+{start}c"
        b = f"1.0+{end}c"
        if tag in ("bold", "italic", "underline", "h1", "h2", "h3", "li"):
            try:
                widget.tag_add(tag if tag != "li" else "list_item", a, b)
            except Exception:
                pass
    widget.edit_modified(False)


def _index_to_offset(widget: Text, index: str) -> int:
    """Character offset from 1.0."""
    try:
        res = widget.count("1.0", index, "chars")
        if res is None:
            return 0
        if isinstance(res, (list, tuple)):
            return int(res[0] or 0)
        return int(res)
    except Exception:
        # Fallback: measure via get length
        return len(widget.get("1.0", index))


def text_widget_to_html(widget: Text) -> str:
    """Export Text content + tags to simple HTML for CS-Cart."""
    raw = widget.get("1.0", "end-1c")
    if not raw.strip():
        return ""

    n = len(raw)
    bold = [False] * (n + 1)
    italic = [False] * (n + 1)
    under = [False] * (n + 1)

    for tag_name, flags in (
        ("bold", bold),
        ("italic", italic),
        ("underline", under),
    ):
        ranges = widget.tag_ranges(tag_name)
        for i in range(0, len(ranges), 2):
            a = _index_to_offset(widget, str(ranges[i]))
            b = _index_to_offset(widget, str(ranges[i + 1]))
            for j in range(max(0, a), min(n, b)):
                flags[j] = True

    def styled_slice(start: int, end: int) -> str:
        if start >= end:
            return ""
        out: list[str] = []
        i = start
        while i < end:
            j = i + 1
            while j < end and bold[j] == bold[i] and italic[j] == italic[i] and under[j] == under[i]:
                j += 1
            chunk = html_lib.escape(raw[i:j])
            if bold[i]:
                chunk = f"<strong>{chunk}</strong>"
            if italic[i]:
                chunk = f"<em>{chunk}</em>"
            if under[i]:
                chunk = f"<u>{chunk}</u>"
            out.append(chunk)
            i = j
        return "".join(out)

    lines = raw.split("\n")
    offset = 0
    blocks: list[str] = []
    ul_items: list[str] = []
    ol_items: list[str] = []

    def flush_ul():
        nonlocal ul_items
        if ul_items:
            blocks.append("<ul>" + "".join(f"<li>{x}</li>" for x in ul_items) + "</ul>")
            ul_items = []

    def flush_ol():
        nonlocal ol_items
        if ol_items:
            blocks.append("<ol>" + "".join(f"<li>{x}</li>" for x in ol_items) + "</ol>")
            ol_items = []

    for line in lines:
        line_start = offset
        line_end = offset + len(line)
        stripped = line.lstrip()
        lead = len(line) - len(stripped)

        m_bullet = re.match(r"^•\s?", stripped)
        m_num = re.match(r"^(\d+)\.\s", stripped)

        if m_bullet:
            flush_ol()
            inner_start = line_start + lead + m_bullet.end()
            ul_items.append(styled_slice(inner_start, line_end) or "&nbsp;")
        elif m_num:
            flush_ul()
            inner_start = line_start + lead + m_num.end()
            ol_items.append(styled_slice(inner_start, line_end) or "&nbsp;")
        elif stripped == "":
            flush_ul()
            flush_ol()
        else:
            flush_ul()
            flush_ol()
            # Detect heading tags covering most of the line
            body = styled_slice(line_start + lead, line_end)
            htag = ""
            for name in ("h1", "h2", "h3"):
                ranges = widget.tag_ranges(name)
                for i in range(0, len(ranges), 2):
                    a = _index_to_offset(widget, str(ranges[i]))
                    b = _index_to_offset(widget, str(ranges[i + 1]))
                    if a <= line_start + lead and b >= line_end - 1:
                        htag = name
                        break
                if htag:
                    break
            if htag:
                # strip style wrappers if duplicate - body already styled
                plain = re.sub(r"</?(?:strong|em|u)>", "", body)
                blocks.append(f"<{htag}>{plain}</{htag}>")
            else:
                blocks.append(f"<p>{body}</p>")

        offset = line_end + 1

    flush_ul()
    flush_ol()
    return "\n".join(blocks) if blocks else f"<p>{html_lib.escape(raw.strip())}</p>"


def configure_rich_text_tags(widget: Text, base_family: str = "Segoe UI") -> None:
    base = tkfont.Font(family=base_family, size=11)
    widget.configure(font=base, spacing1=2, spacing3=4, padx=8, pady=8)
    widget.tag_configure("bold", font=(base_family, 11, "bold"))
    widget.tag_configure("italic", font=(base_family, 11, "italic"))
    widget.tag_configure("underline", underline=1)
    widget.tag_configure("h1", font=(base_family, 16, "bold"), spacing1=8, spacing3=4)
    widget.tag_configure("h2", font=(base_family, 14, "bold"), spacing1=6, spacing3=3)
    widget.tag_configure("h3", font=(base_family, 12, "bold"), spacing1=4, spacing3=2)
    widget.tag_configure("list_item", lmargin1=12, lmargin2=24)
