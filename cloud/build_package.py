#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包阿里云 FC 代码包：代码 + 配置 + 依赖（不含数据、不含浏览器）"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
STAGE = DIST / "stage"

INCLUDE_DIRS = ["collector", "cloud"]
INCLUDE_FILES = ["config.yaml"]
INCLUDE_GLOB = ["docs/index.html"]

REQ = ["requests>=2.31", "pyyaml>=6.0", "beautifulsoup4>=4.12", "openpyxl>=3.1"]


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    STAGE.mkdir(parents=True)

    for d in INCLUDE_DIRS:
        shutil.copytree(ROOT / d, STAGE / d,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in INCLUDE_FILES:
        shutil.copy2(ROOT / f, STAGE / f)
    for g in INCLUDE_GLOB:
        dst = STAGE / g
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / g, dst)

    req = STAGE / "requirements.txt"
    req.write_text("\n".join(REQ) + "\n", encoding="utf-8")

    # 依赖直接打进包内 libs/，避免云端构建阶段的不确定性
    libs = STAGE / "libs"
    print("[build] 安装依赖到 libs/ ...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "--target", str(libs), *REQ],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:])
        raise SystemExit("依赖安装失败")

    for junk in libs.rglob("__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)
    for junk in list(libs.glob("*.dist-info")):
        shutil.rmtree(junk, ignore_errors=True)
    for junk in list(libs.glob("bin")):
        shutil.rmtree(junk, ignore_errors=True)

    out = DIST / "fc-package.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in STAGE.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(STAGE))
    size = out.stat().st_size / 1024 / 1024
    print(f"[build] {out}  {size:.1f} MB")
    if size > 95:
        print("[warn] 超过 100MB 上限，需拆分为层（layer）")
    return out


if __name__ == "__main__":
    main()
