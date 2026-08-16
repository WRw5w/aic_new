from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


NAVY = colors.HexColor("#173B57")
BLUE = colors.HexColor("#246B8E")
GREEN = colors.HexColor("#287A62")
PURPLE = colors.HexColor("#6A4C78")
INK = colors.HexColor("#263746")
MUTED = colors.HexColor("#647786")
PALE = colors.HexColor("#F3F7F9")
LINE = colors.HexColor("#CED9DF")


def register_fonts() -> tuple[str, str]:
    candidates = [
        (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\msyhbd.ttc")),
        (Path(r"C:\Windows\Fonts\simhei.ttf"), Path(r"C:\Windows\Fonts\simhei.ttf")),
        (Path(r"C:\Windows\Fonts\simsun.ttc"), Path(r"C:\Windows\Fonts\simsun.ttc")),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("CJK", str(regular), subfontIndex=0))
            pdfmetrics.registerFont(TTFont("CJK-Bold", str(bold), subfontIndex=0))
            return "CJK", "CJK-Bold"
    raise RuntimeError("No supported CJK font found under C:/Windows/Fonts")


REGULAR_FONT, BOLD_FONT = register_fonts()


def inline_markup(text: str) -> str:
    escaped = html.escape(text.strip())
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<link href="\2" color="#246B8E"><u>\1</u></link>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`]+)`", r'<font name="Courier" color="#6A4C78">\1</font>', escaped)
    return escaped


def is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def parse_table(lines: list[str], start: int, cell_style: ParagraphStyle) -> tuple[LongTable, int]:
    raw_rows: list[list[str]] = []
    index = start
    while index < len(lines) and lines[index].strip().startswith("|"):
        raw_rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
        index += 1
    if len(raw_rows) >= 2 and is_table_separator(lines[start + 1]):
        raw_rows.pop(1)

    columns = max(len(row) for row in raw_rows)
    rows = [row + [""] * (columns - len(row)) for row in raw_rows]
    data = [
        [Paragraph(inline_markup(cell), cell_style) for cell in row]
        for row in rows
    ]
    available = A4[0] - 38 * mm
    lengths = []
    for col in range(columns):
        max_len = max(len(re.sub(r"[*`]", "", row[col])) for row in rows)
        lengths.append(max(5, min(max_len, 36)))
    total = sum(lengths)
    widths = [available * value / total for value in lengths]
    table = LongTable(data, colWidths=widths, repeatRows=1, splitByRow=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    for row_index in range(1, len(data)):
        if row_index % 2 == 0:
            table.setStyle(TableStyle([("BACKGROUND", (0, row_index), (-1, row_index), PALE)]))
    return table, index


class ReportDocTemplate(BaseDocTemplate):
    def __init__(self, filename: str, title: str):
        super().__init__(
            filename,
            pagesize=A4,
            rightMargin=19 * mm,
            leftMargin=19 * mm,
            topMargin=22 * mm,
            bottomMargin=19 * mm,
            title=title,
            author="Codex / 项目组",
        )
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="normal")
        self.addPageTemplates(PageTemplate(id="main", frames=frame, onPage=self.draw_page))

    def draw_page(self, canvas, doc):
        if doc.page <= 1:
            return
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(self.leftMargin, A4[1] - 15 * mm, A4[0] - self.rightMargin, A4[1] - 15 * mm)
        canvas.setFont(REGULAR_FONT, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(self.leftMargin, A4[1] - 11.5 * mm, "2026 AIC 比赛判断与团队交接")
        canvas.drawRightString(A4[0] - self.rightMargin, 10.5 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            level = getattr(flowable, "_toc_level", None)
            if level is not None:
                text = flowable.getPlainText()
                key = f"h{level}-{self.seq.nextf('heading')}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=False)
                self.notify("TOCEntry", (level, text, self.page, key))


def build_styles():
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "BodyCJK",
        parent=base["BodyText"],
        fontName=REGULAR_FONT,
        fontSize=10.2,
        leading=17.2,
        textColor=INK,
        alignment=TA_JUSTIFY,
        spaceAfter=5.5,
        wordWrap="CJK",
        allowWidows=0,
        allowOrphans=0,
    )
    return {
        "body": body,
        "meta": ParagraphStyle(
            "Meta", parent=body, alignment=TA_CENTER, fontSize=9.5, leading=15, textColor=MUTED
        ),
        "title": ParagraphStyle(
            "TitleCJK",
            parent=body,
            fontName=BOLD_FONT,
            fontSize=25,
            leading=36,
            alignment=TA_CENTER,
            textColor=NAVY,
            spaceAfter=14,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=body, fontSize=13, leading=20, alignment=TA_CENTER, textColor=GREEN
        ),
        "h2": ParagraphStyle(
            "H2CJK",
            parent=body,
            fontName=BOLD_FONT,
            fontSize=16,
            leading=23,
            textColor=NAVY,
            spaceBefore=13,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "H3CJK",
            parent=body,
            fontName=BOLD_FONT,
            fontSize=12.8,
            leading=19,
            textColor=GREEN,
            spaceBefore=9,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "h4": ParagraphStyle(
            "H4CJK",
            parent=body,
            fontName=BOLD_FONT,
            fontSize=11.2,
            leading=17,
            textColor=PURPLE,
            spaceBefore=7,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "quote": ParagraphStyle(
            "Quote", parent=body, leftIndent=10, rightIndent=8, borderColor=BLUE, borderWidth=0.8,
            borderPadding=7, backColor=PALE, textColor=colors.HexColor("#405868"), spaceBefore=5, spaceAfter=8
        ),
        "bullet": ParagraphStyle(
            "BulletCJK", parent=body, leftIndent=14, firstLineIndent=-8, bulletIndent=3, spaceAfter=3
        ),
        "code": ParagraphStyle(
            "Code", parent=body, fontName="Courier", fontSize=7.7, leading=11, leftIndent=7,
            rightIndent=7, borderColor=LINE, borderWidth=0.5, borderPadding=7, backColor=colors.HexColor("#F7F8FA")
        ),
        "cell": ParagraphStyle(
            "CellCJK", parent=body, fontSize=8.1, leading=11.4, alignment=TA_LEFT, spaceAfter=0
        ),
        "toc": ParagraphStyle(
            "TOC", parent=body, fontSize=10.2, leading=16, leftIndent=12, firstLineIndent=-8
        ),
    }


def markdown_to_story(markdown_text: str, styles: dict) -> tuple[str, str, list]:
    lines = markdown_text.splitlines()
    title = lines[0].lstrip("# ").strip()
    meta = lines[2].lstrip("> ").strip() if len(lines) > 2 and lines[2].startswith(">") else ""

    story = [
        Spacer(1, 38 * mm),
        Paragraph(inline_markup(title), styles["title"]),
        Spacer(1, 6 * mm),
        Paragraph("横纵分析法深度研究报告", styles["subtitle"]),
        Spacer(1, 11 * mm),
        Table([[""]], colWidths=[70 * mm], rowHeights=[1.2 * mm], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), BLUE)])),
        Spacer(1, 10 * mm),
        Paragraph(inline_markup(meta), styles["meta"]),
        Spacer(1, 42 * mm),
        Paragraph("Codex / 项目组 · 2026-08-16", styles["meta"]),
        PageBreak(),
        Paragraph("目录", styles["h2"]),
    ]
    toc = TableOfContents()
    toc.levelStyles = [styles["toc"], ParagraphStyle("TOC2", parent=styles["toc"], leftIndent=25, fontSize=9.5)]
    story.extend([toc, PageBreak()])

    i = 3
    paragraph_buffer: list[str] = []

    def flush_paragraph():
        if paragraph_buffer:
            text = " ".join(part.strip() for part in paragraph_buffer)
            story.append(Paragraph(inline_markup(text), styles["body"]))
            paragraph_buffer.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            i += 1
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            story.extend([Spacer(1, 3), Preformatted("\n".join(code_lines), styles["code"]), Spacer(1, 5)])
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            flush_paragraph()
            table, i = parse_table(lines, i, styles["cell"])
            story.extend([Spacer(1, 4), table, Spacer(1, 7)])
            continue
        heading = re.match(r"^(#{2,4})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            level = len(heading.group(1)) - 2
            style_name = {0: "h2", 1: "h3", 2: "h4"}[level]
            p = Paragraph(inline_markup(heading.group(2)), styles[style_name])
            p._toc_level = min(level, 1)
            story.append(p)
            i += 1
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            story.append(Paragraph(inline_markup(stripped.lstrip("> ")), styles["quote"]))
            i += 1
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        numbered = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if bullet or numbered:
            flush_paragraph()
            marker = "•" if bullet else f"{numbered.group(1)}."
            content = bullet.group(1) if bullet else numbered.group(2)
            story.append(Paragraph(f"{marker} {inline_markup(content)}", styles["bullet"]))
            i += 1
            continue
        paragraph_buffer.append(stripped)
        i += 1

    flush_paragraph()
    return title, meta, story


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    styles = build_styles()
    title, _, story = markdown_to_story(args.input.read_text(encoding="utf-8"), styles)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc = ReportDocTemplate(str(args.output), title=title)
    doc.multiBuild(story)
    print(f"created {args.output}")


if __name__ == "__main__":
    main()
