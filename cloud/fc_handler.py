#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里云函数计算（FC）入口
========================
定时触发 → 国内网络采集全部信源 → 生成 JSON/Excel/单文件网页 → 提交到 GitHub 仓库
GitHub Pages 检测到 docs/ 变化后自动重新发布。

函数配置（控制台或 deploy_fc.py 自动设置）：
  运行时      Python 3.10/3.11
  入口        fc_handler.handler
  超时        1800 秒（采集 60+ 源约 20-30 分钟）
  内存        1024 MB
  环境变量    LAW_ROOT=/code
              LAW_OUT_DIR=/tmp/law-out
              GH_TOKEN=<细粒度令牌>
              GH_REPO=panyanosakana/carbon-law-watch
              GH_BRANCH=main
"""
import json
import os
import sys
import traceback
from pathlib import Path

CODE_DIR = Path(os.environ.get("LAW_ROOT", "/code"))
OUT_DIR = Path(os.environ.get("LAW_OUT_DIR", "/tmp/law-out"))
sys.path.insert(0, str(CODE_DIR / "libs"))          # 包内依赖（离线可用）
sys.path.insert(0, str(CODE_DIR / "collector"))
sys.path.insert(0, str(CODE_DIR / "cloud"))


def _collect_artifacts(cfg) -> dict:
    """把产物映射成「仓库内路径 → 本地文件」"""
    files = {}
    json_rel = cfg["output"]["json"]                     # data/regulations.json
    files[json_rel] = OUT_DIR / json_rel
    files[f"docs/{json_rel}"] = OUT_DIR / "docs" / json_rel

    excel_dir = OUT_DIR / cfg["output"]["excel_dir"]
    for f in sorted(excel_dir.glob("*.xlsx")) if excel_dir.exists() else []:
        files[f"{cfg['output']['excel_dir']}/{f.name}"] = f

    standalone = OUT_DIR / cfg["output"].get("standalone", "双碳法规监测-单文件版.html")
    if standalone.exists():
        files[standalone.name] = standalone

    return {k: v for k, v in files.items() if Path(v).exists()}


def handler(event, context):
    """event 可为空；支持 {"dry_run": true} 只采集不提交。"""
    started = __import__("datetime").datetime.now()
    dry_run = False
    try:
        if isinstance(event, (bytes, bytearray)):
            event = json.loads(event.decode("utf-8") or "{}")
        if isinstance(event, str):
            event = json.loads(event or "{}")
        if isinstance(event, dict):
            dry_run = bool(event.get("dry_run"))
    except Exception:
        pass

    try:
        import yaml
        from engine_v3 import main as run_engine

        payload = run_engine()

        cfg = yaml.safe_load((CODE_DIR / "config.yaml").read_text(encoding="utf-8"))
        files = _collect_artifacts(cfg)
        result = {"ok": True, "month": payload["month"], "count": payload["count"],
                  "sources_ok": sum(1 for s in payload["sources"] if s["ok"]),
                  "sources_total": len(payload["sources"]),
                  "artifacts": list(files)}

        if dry_run:
            result["push"] = "dry_run 跳过提交"
        else:
            from github_push import commit_files
            push = commit_files(files, f"法规更新 {payload['month']}（阿里云 FC 采集）")
            result["push"] = push

        result["elapsed_s"] = int((__import__("datetime").datetime.now() - started).total_seconds())
        print(json.dumps(result, ensure_ascii=False))
        return result

    except Exception as e:
        print(traceback.format_exc())
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    # 本地/云端调试：python3 cloud/fc_handler.py '{"dry_run": true}'
    arg = sys.argv[1] if len(sys.argv) > 1 else "{}"
    print(json.dumps(handler(arg, None), ensure_ascii=False, indent=2))
