#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里云函数计算一键部署（幂等：可重复执行）
==========================================
用法：
  export ALIBABA_CLOUD_ACCESS_KEY_ID=xxx
  export ALIBABA_CLOUD_ACCESS_KEY_SECRET=xxx
  export GH_TOKEN=github_pat_xxx          # 只需目标仓库 Contents 读写
  python3 cloud/deploy_fc.py              # 默认杭州地域、函数名 carbon-law-collector

常用参数（环境变量）：
  FC_REGION       默认 cn-hangzhou
  FC_FUNCTION     默认 carbon-law-collector
  FC_CRON         默认 "0 30 8 * * *"     # 每天 08:30（Asia/Shanghai）
  GH_REPO         默认 panyanosakana/carbon-law-watch
  SKIP_TRIGGER=1  只更新代码，不动触发器
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZIP = ROOT / "dist" / "fc-package.zip"

REGION = os.environ.get("FC_REGION", "cn-hangzhou")
FUNC = os.environ.get("FC_FUNCTION", "carbon-law-collector")
CRON = os.environ.get("FC_CRON", "0 30 8 * * *")
GH_REPO = os.environ.get("GH_REPO", "panyanosakana/carbon-law-watch")

RUNTIME = "python3.10"
HANDLER = "fc_handler.handler"
MEMORY = 1024
TIMEOUT = 1800          # 30 分钟（60+ 源最长约 30 分钟）


def env_vars() -> dict:
    gh = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not gh:
        raise SystemExit("缺少 GH_TOKEN")
    return {
        "LAW_ROOT": "/code",
        "LAW_OUT_DIR": "/tmp/law-out",
        "GH_TOKEN": gh,
        "GH_REPO": GH_REPO,
        "GH_BRANCH": os.environ.get("GH_BRANCH", "main"),
        "PYTHONUNBUFFERED": "1",
    }


def main():
    if not ZIP.exists():
        raise SystemExit("缺少代码包，请先运行 python3 cloud/build_package.py")

    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi import models as open_api_models

    cfg = open_api_models.Config(
        access_key_id=os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"],
        access_key_secret=os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"],
        endpoint=f"fcv3.{REGION}.aliyuncs.com",
    )
    cli = Client(cfg)
    zip_b64 = base64.b64encode(ZIP.read_bytes()).decode("ascii")
    print(f"[deploy] 代码包 {ZIP.stat().st_size / 1024 / 1024:.1f} MB → {FUNC}@{REGION}")

    code = m.InputCode(zip_file=zip_b64)
    exists = True
    try:
        cur = cli.get_function(FUNC)
        print(f"[deploy] 函数已存在（当前 {cur.body.runtime} / {cur.body.timeout}s）")
    except Exception as e:
        if "NotFound" in type(e).__name__ or "404" in str(e):
            exists = False
        else:
            raise

    if exists:
        req = m.UpdateFunctionRequest(
            runtime=RUNTIME, handler=HANDLER, code=code,
            memory_size=MEMORY, timeout=TIMEOUT,
            environment_variables=env_vars(),
            description="双碳法规自动采集（国内网络）",
        )
        cli.update_function(FUNC, req)
        print("[deploy] 函数已更新")
    else:
        req = m.CreateFunctionRequest(
            function_name=FUNC, runtime=RUNTIME, handler=HANDLER, code=code,
            memory_size=MEMORY, timeout=TIMEOUT,
            environment_variables=env_vars(),
            description="双碳法规自动采集（国内网络）",
        )
        cli.create_function(req)
        print("[deploy] 函数已创建")

    if os.environ.get("SKIP_TRIGGER") == "1":
        print("[deploy] 跳过触发器")
        return

    trigger_name = "daily-collect"
    trig_cfg = json.dumps({
        "payload": "{}",
        "cronExpression": CRON,
        "enable": True,
    }, ensure_ascii=False)
    try:
        cli.get_trigger(FUNC, trigger_name)
        cli.update_trigger(FUNC, trigger_name, m.UpdateTriggerRequest(trigger_config=trig_cfg))
        print(f"[deploy] 触发器已更新：{CRON}")
    except Exception:
        cli.create_trigger(FUNC, m.CreateTriggerRequest(
            trigger_name=trigger_name, trigger_type="timer",
            trigger_config=trig_cfg, description="每天定时采集"))
        print(f"[deploy] 触发器已创建：{CRON}（Asia/Shanghai）")

    # 立即试跑一次
    try:
        inv = cli.invoke_function(FUNC, m.InvokeFunctionRequest(body=b'{"dry_run": true}'))
        print(f"[deploy] 首次调用已提交，请求 ID：{getattr(inv.body, 'request_id', '-')}")
        print("[deploy] 到控制台「函数日志」查看采集进度（约 20-30 分钟）")
    except Exception as e:
        print(f"[deploy] 调用未成功（可稍后在控制台手动测试）：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
