#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""V3 Excel 导出：法规清单 + 附表一/三/四/五 + 采集概览"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

DEEP = "1F5C3D"
LIGHT = "EDF4EF"
WARN = "C0392B"
BORDER = Border(*[Side(style="thin", color="CFDCD4")] * 4)
H_FONT = Font(name="微软雅黑", size=10.5, bold=True, color="FFFFFF")
BODY = Font(name="微软雅黑", size=10)
TITLE_FONT = Font(name="微软雅黑", size=14, bold=True, color=DEEP)


def _head(ws, headers, widths, freeze="A2"):
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for c in ws[1]:
        c.font = H_FONT
        c.fill = PatternFill("solid", fgColor=DEEP)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = freeze


def _body(ws, row, ncol, center_cols=(), height=None):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = BODY
        cell.border = BORDER
        cell.alignment = Alignment(vertical="top", wrap_text=True,
                                   horizontal="center" if c in center_cols else "left")
    if height:
        ws.row_dimensions[row].height = height


def export(payload: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "法规清单"

    headers = ["编号", "发布日期", "周次", "层级", "地区", "发文机关", "文号", "文件全称",
               "领域", "性质", "核心要点（原文摘抄）", "补充说明", "官方链接"]
    widths = [6, 12, 17, 8, 8, 22, 24, 44, 20, 11, 76, 26, 44]
    _head(ws, headers, widths)

    for it in payload["items"]:
        pts = "\n".join(f"{n}. 【{p['label']}】{p['text']}"
                        for n, p in enumerate(it.get("points") or [], 1))
        if not pts:
            pts = ("（正文为 PDF/OFD 附件，无可摘抄正文，请查阅原文）"
                   if it.get("attach_only") else "（页面未提供可摘抄正文，请查阅原文）")
        ws.append([it["no"], it.get("date", ""), it.get("week", ""), it.get("level", ""),
                   it.get("region", ""), it.get("publisher", ""), it.get("doc_no") or "以正式文本为准",
                   it.get("title", ""), "、".join(it.get("domains") or []) or "—",
                   it.get("nature", ""), pts, it.get("note", ""), it.get("url", "")])
        r = ws.max_row
        _body(ws, r, len(headers), center_cols=(1, 2, 3, 4, 5, 10),
              height=max(40, 15 * max(len(it.get("points") or []), 1) + 16))
        link = ws.cell(row=r, column=13)
        if it.get("url", "").startswith("http"):
            link.hyperlink = it["url"]
            link.font = Font(name="微软雅黑", size=9, color="1A5FB4", underline="single")
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"

    # 附表一·时间轴
    ws1 = wb.create_sheet("附表一·时间轴")
    _head(ws1, ["编号", "时间", "层级", "文件简称", "文号", "领域"],
          [7, 13, 9, 56, 26, 24])
    for it in payload["items"]:
        ws1.append([it["no"], it.get("date", ""), it.get("level", ""),
                    it.get("title", "")[:60], it.get("doc_no") or "—",
                    "、".join(it.get("domains") or [])])
        _body(ws1, ws1.max_row, 6, center_cols=(1, 2, 3))

    # 附表三·覆盖度自查
    ws3 = wb.create_sheet("附表三·覆盖度自查")
    ws3["A1"] = "覆盖度自查（部门 / 地区 / 领域三维）"
    ws3["A1"].font = TITLE_FONT
    ws3.merge_cells("A1:D1")
    row = 3
    ws3.cell(row=row, column=1, value="① 部门维度（8 个重点部委）").font = Font(bold=True, color=DEEP)
    row += 1
    for c, v in enumerate(["部门", "本月条数", "状态", "条目编号"], 1):
        cell = ws3.cell(row=row, column=c, value=v)
        cell.font = H_FONT
        cell.fill = PatternFill("solid", fgColor=DEEP)
        cell.border = BORDER
    for m in payload["coverage"]["ministries"]:
        row += 1
        ws3.cell(row=row, column=1, value=m["name"])
        ws3.cell(row=row, column=2, value=m["count"])
        ws3.cell(row=row, column=3, value=m["status"])
        ws3.cell(row=row, column=4, value="、".join(map(str, m["ids"])) or "—")
        _body(ws3, row, 4, center_cols=(2,))

    row += 2
    ws3.cell(row=row, column=1, value="② 地区维度").font = Font(bold=True, color=DEEP)
    row += 1
    for c, v in enumerate(["地区", "本月条数", "条目编号"], 1):
        cell = ws3.cell(row=row, column=c, value=v)
        cell.font = H_FONT
        cell.fill = PatternFill("solid", fgColor=DEEP)
        cell.border = BORDER
    for r in payload["coverage"]["regions"]:
        row += 1
        ws3.cell(row=row, column=1, value=r["name"])
        ws3.cell(row=row, column=2, value=r["count"])
        ws3.cell(row=row, column=3, value="、".join(map(str, r["ids"])) or "—")
        _body(ws3, row, 3, center_cols=(2,))

    row += 2
    ws3.cell(row=row, column=1, value="③ 领域维度（27 个，含两个专项）").font = Font(bold=True, color=DEEP)
    row += 1
    for c, v in enumerate(["领域", "本月条数", "状态", "条目编号"], 1):
        cell = ws3.cell(row=row, column=c, value=v)
        cell.font = H_FONT
        cell.fill = PatternFill("solid", fgColor=DEEP)
        cell.border = BORDER
    for d in payload["coverage"]["domains"]:
        row += 1
        ws3.cell(row=row, column=1, value=d["name"])
        ws3.cell(row=row, column=2, value=d["count"])
        ws3.cell(row=row, column=3, value=d["status"])
        ws3.cell(row=row, column=4, value="、".join(map(str, d["ids"])) or "—")
        _body(ws3, row, 4, center_cols=(2,))
        if not d["count"]:
            ws3.cell(row=row, column=3).font = Font(name="微软雅黑", size=10, color=WARN)
    ws3.column_dimensions["A"].width = 30
    ws3.column_dimensions["B"].width = 11
    ws3.column_dimensions["C"].width = 22
    ws3.column_dimensions["D"].width = 24

    # 附表四·无新增领域与最近参考
    ws4 = wb.create_sheet("附表四·无新增领域")
    _head(ws4, ["领域", "最近一次相关文件", "发布时间", "发文机关", "链接"],
          [30, 56, 13, 24, 50])
    for n in payload["no_hit_domains"]:
        ws4.append([n["name"], n["last_title"] or "本月及参考期内均未检索到",
                    n["last_date"] or "—", n["last_publisher"] or "—", n["last_url"] or "—"])
        _body(ws4, ws4.max_row, 5, center_cols=(3,))
        cell = ws4.cell(row=ws4.max_row, column=5)
        if (n["last_url"] or "").startswith("http"):
            cell.hyperlink = n["last_url"]
            cell.font = Font(name="微软雅黑", size=9, color="1A5FB4", underline="single")

    # 附表五·待核项
    ws5 = wb.create_sheet("附表五·待核项")
    _head(ws5, ["事项", "状态"], [70, 60])
    for p in payload["pending"]:
        ws5.append([p["item"], p["status"]])
        _body(ws5, ws5.max_row, 2)

    # 采集概览
    ws6 = wb.create_sheet("采集概览")
    info = [
        ("专业信息收集助手 · 采集概览（V3）", ""),
        ("生成时间", payload["updated_at"]),
        ("统计月份", payload["month"]),
        ("入库条数", payload["count"]),
        ("排序规则", payload["sort_rule"]),
        ("时间口径", payload["time_rule"]),
        ("信息源", f"成功 {sum(1 for s in payload['sources'] if s['ok'])} / 共 {len(payload['sources'])}"),
        ("领域覆盖", f"{sum(1 for d in payload['coverage']['domains'] if d['count'])} / 27"),
        ("说明", "要点为原文句子直接摘抄并标注归类，未做改写；无正文可摘抄的文件已如实标注。"),
    ]
    for r in info:
        ws6.append(list(r))
    ws6["A1"].font = TITLE_FONT
    ws6.merge_cells("A1:C1")
    for r in range(2, len(info) + 1):
        ws6.cell(row=r, column=1).font = Font(name="微软雅黑", size=10, bold=True)
        ws6.cell(row=r, column=2).font = BODY
        ws6.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    ws6.column_dimensions["A"].width = 16
    ws6.column_dimensions["B"].width = 90

    start = len(info) + 3
    for c, v in enumerate(["信息源", "分组", "方式", "状态", "候选", "入库", "备注"], 1):
        cell = ws6.cell(row=start, column=c, value=v)
        cell.font = H_FONT
        cell.fill = PatternFill("solid", fgColor=DEEP)
        cell.border = BORDER
    for s in payload["sources"]:
        ws6.append([s["name"], s.get("group", ""), s.get("adapter", ""),
                    "正常" if s["ok"] else "未成功", s.get("candidates", 0), s.get("hit", 0),
                    s.get("error", "")])
        _body(ws6, ws6.max_row, 7, center_cols=(4, 5, 6))

    path = out_dir / f"双碳法规汇总-{payload['month']}.xlsx"
    wb.save(path)
    return path
