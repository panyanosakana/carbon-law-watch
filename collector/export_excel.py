#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""把采集结果导出为 Excel（法规清单 + 采集概览两个工作表）"""
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

GREEN = "1F5C3D"
LIGHT = "EAF3EE"
BORDER = Border(*[Side(style="thin", color="C9D6CE")] * 4)
HEAD_FONT = Font(name="微软雅黑", size=10.5, bold=True, color="FFFFFF")
BODY_FONT = Font(name="微软雅黑", size=10)

HEADERS = ["序号", "发布日期", "地区", "发文机关", "文号", "法规名称",
           "类别", "核心要点（原文摘抄）", "官方链接", "采集时间"]
WIDTHS = [6, 12, 9, 22, 24, 46, 13, 78, 46, 17]


def export(payload: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # ---------- Sheet 1：法规清单 ----------
    ws = wb.active
    ws.title = "法规清单"
    ws.append(HEADERS)
    for i, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for cell in ws[1]:
        cell.font = HEAD_FONT
        cell.fill = PatternFill("solid", fgColor=GREEN)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    for idx, it in enumerate(payload["items"], 1):
        points = it.get("points") or []
        if points:
            pts = "\n".join(f"{n}. {p}" for n, p in enumerate(points, 1))
        elif it.get("attach_only"):
            pts = "（正文以 PDF/OFD 附件形式发布，无网页正文可摘抄，请点击链接查阅原文）"
        else:
            pts = "（页面未提供可摘抄正文，请点击链接查阅原文）"
        ws.append([idx, it.get("date", ""), it.get("region", ""), it.get("publisher", ""),
                   it.get("doc_no") or "—", it.get("title", ""), it.get("category", ""),
                   pts, it.get("url", ""), payload["updated_at"]])

        r = ws.max_row
        for c in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True,
                                       horizontal="center" if c in (1, 2, 3, 7) else "left")
        link = ws.cell(row=r, column=9)
        if it.get("url", "").startswith("http"):
            link.hyperlink = it["url"]
            link.font = Font(name="微软雅黑", size=9, color="1A5FB4", underline="single")
        ws.row_dimensions[r].height = max(38, 15 * max(len(points), 1) + 12)
        if idx % 2 == 0:
            for c in range(1, len(HEADERS) + 1):
                ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=LIGHT)

    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{ws.max_row}"

    # ---------- Sheet 2：采集概览 ----------
    ws2 = wb.create_sheet("采集概览")
    ws2.column_dimensions["A"].width = 16
    ws2.column_dimensions["B"].width = 26
    ws2.column_dimensions["C"].width = 12
    ws2.column_dimensions["D"].width = 10
    ws2.column_dimensions["E"].width = 10
    ws2.column_dimensions["F"].width = 56

    ok = sum(1 for s in payload["sources"] if s["ok"])
    dim = payload["dimensions"]
    info = [
        ("双碳法规自动收集 · 采集概览", ""),
        ("生成时间", payload["updated_at"]),
        ("统计月份", payload["month"]),
        ("入库条数", payload["count"]),
        ("信息源", f"成功 {ok} / 共 {len(payload['sources'])}"),
        ("涉及地区", "、".join(dim["regions"]) or "—"),
        ("发文机关", f"{len(dim['publishers'])} 个"),
        ("文件类别", "、".join(dim["categories"]) or "—"),
        ("", ""),
        ("说明", "仅收录当月发布内容；“核心要点”为原文句子直接摘抄，未做改写或推断；"),
        ("", "文号/机关优先取原文，缺失时依据标题与来源标注；链接均指向发文机关官方页面。"),
    ]
    for row in info:
        ws2.append(list(row))
    ws2["A1"].font = Font(name="微软雅黑", size=14, bold=True, color=GREEN)
    ws2.merge_cells("A1:F1")
    for r in range(2, len(info) + 1):
        ws2.cell(row=r, column=1).font = Font(name="微软雅黑", size=10, bold=True)
        ws2.cell(row=r, column=2).font = BODY_FONT
        ws2.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")

    start = len(info) + 3
    hdr = ["信息源", "采集方式", "状态", "候选数", "入库数", "备注"]
    ws2.append([])
    ws2.append(hdr)
    for c in range(1, 7):
        cell = ws2.cell(row=start + 1, column=c)
        cell.font = HEAD_FONT
        cell.fill = PatternFill("solid", fgColor=GREEN)
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for s in payload["sources"]:
        ws2.append([s["name"], s.get("adapter", ""), "正常" if s["ok"] else "未成功",
                    s.get("candidates", 0), s.get("hit", 0), s.get("error", "")])
        r = ws2.max_row
        for c in range(1, 7):
            cell = ws2.cell(row=r, column=c)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=(c == 6))
        if not s["ok"]:
            ws2.cell(row=r, column=3).font = Font(name="微软雅黑", size=10, color="C0392B")

    path = out_dir / f"双碳法规汇总-{payload['month']}.xlsx"
    wb.save(path)
    return path
