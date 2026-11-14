#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里云函数计算一键部署（幂等，可重复执行）
==========================================
前置：
  pip3 install alibabacloud_fc20230330 alibabacloud_tea_openapi

用法：
  export ALIBABA_CLOUD_ACCESS_KEY_ID=xxx
  export ALIBABA_CLOUD_ACCESS_KEY_SECRET=xxx
  export GH_TOKEN=github_pat_xxx          # 细粒度令牌，仅目标仓库 Contents 读写
  python3 cloud/build_package.py
  python3 cloud/deploy_fc.py

可选环境变量：
  FC_REGION    默认 cn-hangzhou
  FC_FUNCTION  默认 carbon-law-collector
  FC_CRON      默认 "0 30 8 * * *"（秒 分 时 日 月 周，Asia/Shanghai）
  GH_REPO      默认 panyanosakana/carbon-law-watch
  SKIP_TRIGGER 设为 1 则只更新代码
  NO_INVOKE    设为 1 则部署后不自动试跑
"""
import base64
import json
import os
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
TIMEOUT = 1800


def env_vars() -> dict:
    gh = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not gh:
        raise SystemExit("缺少 GH_TOKEN（GitHub 细粒度令牌，需 Contents 读写）")
    return {
        "LAW_ROOT": "/code",
        "LAW_OUT_DIR": "/tmp/law-out",
        "GH_TOKEN": gh,
        "GH_REPO": GH_REPO,
        "GH_BRANCH": os.environ.get("GH_BRANCH", "main"),
        "PYTHONUNBUFFERED": "1",
    }


def build_client():
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_tea_openapi import models as open_api_models

    ak = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
    sk = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    if not ak or not sk:
        raise SystemExit("缺少 ALIBABA_CLOUD_ACCESS_KEY_ID / _SECRET（RAM 子账号，仅 AliyunFCFullAccess）")
    cfg = open_api_models.Config(access_key_id=ak, access_key_secret=sk,
                                 endpoint=f"fcv3.{REGION}.aliyuncs.com")
    return Client(cfg)


def deploy_function(cli):
    from alibabacloud_fc20230330 import models as m

    if not ZIP.exists():
        raise SystemExit("缺少代码包，请先执行 python3 cloud/build_package.py")
    code = m.InputCodeLocation(zip_file=base64.b64encode(ZIP.read_bytes()).decode("ascii"))
    common = dict(code=code, runtime=RUNTIME, handler=HANDLER, memory_size=MEMORY,
                  timeout=TIMEOUT, environment_variables=env_vars(),
                  description="双碳法规自动采集（国内网络执行，结果提交回 GitHub）")

    exists = True
    try:
        cur = cli.get_function(FUNC, m.GetFunctionRequest())
        print(f"[deploy] 函数已存在：{cur.body.runtime} / {cur.body.timeout}s / {cur.body.memory_size}MB")
    except Exception as e:
        if "NotFound" in type(e).__name__ or "404" in str(e) or "FunctionNotFound" in str(e):
            exists = False
        else:
            raise SystemExit(f"查询函数失败：{type(e).__name__}: {e}")

    if exists:
        cli.update_function(FUNC, m.UpdateFunctionRequest(body=m.UpdateFunctionInput(**common)))
        print("[deploy] 代码与配置已更新")
    else:
        cli.create_function(m.CreateFunctionRequest(body=m.CreateFunctionInput(
            function_name=FUNC, **common)))
        print("[deploy] 函数已创建")


def deploy_trigger(cli):
    from alibabacloud_fc20230330 import models as m

    conf = json.dumps({"cronExpression": CRON, "enable": True, "payload": "{}"},
                      ensure_ascii=False)
    name = "daily-collect"
    try:
        cli.get_trigger(FUNC, name)
        cli.update_trigger(FUNC, name, m.UpdateTriggerRequest(
            body=m.UpdateTriggerInput(trigger_config=conf)))
        print(f"[deploy] 定时触发器已更新：{CRON}")
    except Exception:
        cli.create_trigger(FUNC, m.CreateTriggerRequest(body=m.CreateTriggerInput(
            trigger_name=name, trigger_type="timer", trigger_config=conf,
            description="每天定时采集（Asia/Shanghai）")))
        print(f"[deploy] 定时触发器已创建：{CRON}")


def smoke_test(cli):
    from alibabacloud_fc20230330 import models as m
    try:
        r = cli.invoke_function(FUNC, m.InvokeFunctionRequest(body=b'{"dry_run": true}'))
        print(f"[deploy] 已触发一次试跑（dry_run，不提交），请求 ID：{getattr(r.body, 'request_id', '-')}")
        print("[deploy] 到控制台「调用日志」查看进度，约 20-30 分钟")
    except Exception as e:
        print(f"[deploy] 试跑未成功（可在控制台手动测试）：{type(e).__name__}: {str(e)[:150]}")


def main():
    cli = build_client()
    print(f"[deploy] 目标：{FUNC} @ {REGION}，代码包 {ZIP.stat().st_size / 1024 / 1024:.1f} MB"
          if ZIP.exists() else "[deploy] 目标函数：准备创建")
    deploy_function(cli)
    if os.environ.get("SKIP_TRIGGER") != "1":
        deploy_trigger(cli)
    if os.environ.get("NO_INVOKE") != "1":
        smoke_test(cli)


if __name__ == "__main__":
    main()
