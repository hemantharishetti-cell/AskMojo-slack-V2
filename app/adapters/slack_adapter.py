"""
Consolidated Slack message formatting.

Replaces TWO independent formatters:
  1. format_answer_for_slack() in routes.py (line 925)
  2. format_slack_message() + format_markdown() in socket_mode.py (line 82)

Now there is ONE canonical formatter.
"""

from __future__ import annotations

import re


def _parse_markdown_table_row(line: str) -> list[str] | None:
    stripped = (line or "").strip()
    if "|" not in stripped:
        return None
    if not stripped.startswith("|") and not stripped.endswith("|"):
        return None
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return cells if any(cells) else None


def _is_markdown_separator_row(cells: list[str] | None) -> bool:
    if not cells:
        return False
    for cell in cells:
        token = cell.replace(":", "").replace("-", "").strip()
        if token:
            return False
    return True


def _convert_markdown_tables_to_slack(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0

    while i < len(lines):
        header_cells = _parse_markdown_table_row(lines[i])
        sep_cells = _parse_markdown_table_row(lines[i + 1]) if i + 1 < len(lines) else None
        if header_cells and sep_cells and _is_markdown_separator_row(sep_cells):
            headers = header_cells
            i += 2
            row_index = 0
            while i < len(lines):
                row_cells = _parse_markdown_table_row(lines[i])
                if not row_cells or _is_markdown_separator_row(row_cells):
                    break
                pairs = []
                for idx, header in enumerate(headers):
                    value = row_cells[idx].strip() if idx < len(row_cells) else ""
                    if header and value:
                        pairs.append(f"*{header}*: {value}")
                    elif value:
                        pairs.append(value)
                if pairs:
                    prefix = "• " if row_index > 0 else ""
                    out.append(prefix + " | ".join(pairs))
                row_index += 1
                i += 1
            out.append("")
            continue

        out.append(lines[i])
        i += 1

    return "\n".join(out)


def format_for_slack(answer: str) -> str:
    """
    Convert markdown-formatted answer text to Slack mrkdwn format.

    This is the canonical text formatter that replaces both
    `format_answer_for_slack()` and `format_markdown()`.
    """
    text = _convert_markdown_tables_to_slack(answer)

    # Markdown headings → Slack bold
    text = re.sub(r"^#### (.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"^### (.+)$", r"\n*\1*\n", text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$", r"\n*\1*\n", text, flags=re.MULTILINE)
    text = re.sub(r"^# (.+)$", r"\n*\1*\n", text, flags=re.MULTILINE)

    # **bold** → *bold* (Slack uses single asterisks)
    text = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", text)

    # Nested bullet points → Slack-friendly bullets
    text = re.sub(r"^      - ", r"        • ", text, flags=re.MULTILINE)
    text = re.sub(r"^   - ", r"      • ", text, flags=re.MULTILINE)
    text = re.sub(r"^- ", r"• ", text, flags=re.MULTILINE)

    # Markdown links → Slack links
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"<\2|\1>", text)

    # Horizontal rules
    text = re.sub(r"^---$", "─────────────────────────", text, flags=re.MULTILINE)

    # References section
    text = re.sub(
        r"^### References$",
        "\n─────────────────────────\n*References:*\n",
        text,
        flags=re.MULTILINE,
    )

    # Clean up excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def format_as_blocks(text: str) -> dict:
    """
    Convert text into Slack Block Kit format for rich rendering.

    Returns a dict with a ``blocks`` key suitable for
    ``client.chat_postMessage(blocks=...)``.
    """
    blocks: list[dict] = []

    # Split into major sections
    sections = re.split(r"\n\n+", text)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        lines = section.split("\n")
        list_items: list[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            list_match = re.match(r"^([\-\*•]|\d+[\.\)])\s+(.+)$", line)
            if list_match:
                item_text = format_for_slack(list_match.group(2))
                list_items.append(item_text)
            else:
                # Flush accumulated list items
                if list_items:
                    list_text = "\n".join(f"• {item}" for item in list_items)
                    blocks.append(_mrkdwn_block(list_text))
                    list_items = []

                blocks.append(_mrkdwn_block(format_for_slack(line)))

        # Flush trailing list
        if list_items:
            list_text = "\n".join(f"• {item}" for item in list_items)
            blocks.append(_mrkdwn_block(list_text))

    if not blocks:
        blocks.append(_mrkdwn_block(format_for_slack(text)))

    return {"blocks": blocks}


def _mrkdwn_block(text: str) -> dict:
    """Create a single Slack mrkdwn section block."""
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text},
    }
