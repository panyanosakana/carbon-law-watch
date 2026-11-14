# 国内部署：阿里云函数计算（FC）

## 为什么需要这一步

GitHub Actions 的服务器在美国机房，实测访问国内政务站时：

- **19 个源连不上**（自然资源部、住建部、河北/山西/四川/陕西工信厅、深圳/重庆/大连工信局等）——政府站点屏蔽境外 IP
- **4 个源被 WAF 拦截**（天津工信局 403、河南工信厅 403、唐山工信局 405、上海环交所 420）

把采集搬到国内网络执行，这些源就能恢复正常，其余逻辑完全不变。

## 架构

```
阿里云 FC 定时触发器（每天 08:30 北京时间）
   └─ 国内网络执行 collector/（60+ 源全部可达）
        └─ 生成 JSON / Excel / 单文件网页
             └─ 调用 GitHub API 提交到仓库（无需 git 二进制）
                  └─ GitHub Pages 检测到 docs/ 变化，自动重新发布
```

网站、Excel、数据结构、筛选规则都不需要改动。

## 方式 A：一键部署（推荐，需 AccessKey）

### 1. 准备三样东西

| 项目 | 获取方式 |
|---|---|
| 阿里云账号 | 注册并实名认证 |
| RAM 子账号 AccessKey | 控制台 → RAM 访问控制 → 用户 → 创建用户（**不要**勾选控制台登录）→ 只授予 `AliyunFCFullAccess` |
| GitHub 细粒度令牌 | GitHub → Settings → Developer settings → Fine-grained tokens → 仅选这一个仓库 → **Contents: Read and write** |

> 免费额度：https://free.aliyun.com 搜「函数计算 FC」领取。按每天跑一次、每次 1024MB×30 分钟计算，月用量约为免费额度的 4%，实际支出 0 元。

### 2. 执行

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=你的AK
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=你的Secret
export GH_TOKEN=github_pat_xxx

python3 cloud/build_package.py     # 打包（约 1.8 MB）
python3 cloud/deploy_fc.py         # 创建函数 + 定时触发器 + 试跑一次
```

脚本是幂等的，重复执行只会更新代码，不会重复建函数。常用可选参数：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `FC_REGION` | `cn-hangzhou` | 地域 |
| `FC_FUNCTION` | `carbon-law-collector` | 函数名 |
| `FC_CRON` | `0 30 8 * * *` | 6 位 cron（秒 分 时 日 月 周），Asia/Shanghai |
| `GH_REPO` | `panyanosakana/carbon-law-watch` | 目标仓库 |
| `SKIP_TRIGGER` | 空 | 设为 1 时只更新代码 |

## 方式 B：控制台手动部署（无需给我 AccessKey）

1. 本地执行 `python3 cloud/build_package.py`，得到 `dist/fc-package.zip`
2. 函数计算控制台 → **创建函数** → 选择「使用示例代码创建」→ 运行环境 `Python 3.10`
3. 函数名称填 `carbon-law-collector`，**请求处理程序**填 `fc_handler.handler`
4. 创建后进入「代码」页，上传 `dist/fc-package.zip`
5. 「配置」页：内存 **1024 MB**，超时时间 **1800 秒**
6. 「配置」→ 环境变量，逐条添加：

   | 键 | 值 |
   |---|---|
   | `LAW_ROOT` | `/code` |
   | `LAW_OUT_DIR` | `/tmp/law-out` |
   | `GH_TOKEN` | 你的 GitHub 细粒度令牌 |
   | `GH_REPO` | `panyanosakana/carbon-law-watch` |
   | `GH_BRANCH` | `main` |

7. 「触发器」→ 创建触发器 → 类型选**定时触发器** → Cron 表达式 `0 30 8 * * *` → 时区 `Asia/Shanghai`
8. 「代码」页点**测试函数**，事件内容填 `{"dry_run": true}`（只采集不提交），先验证能跑通

## 验证

| 检查点 | 期望结果 |
|---|---|
| 函数日志末尾 | `[done] 源 5x/6x 成功 | 当月 N 条 | 领域覆盖 M/27` |
| 函数返回 | `{"ok": true, "count": N, "sources_ok": 5x, "push": {"committed": true, ...}}` |
| GitHub 仓库 | 多一条 `法规更新 2026-11（阿里云 FC 采集）` 提交 |
| 网站 | 打开 `https://panyanosakana.github.io/carbon-law-watch/`，数据随之更新 |

## 与 GitHub Actions 的关系

两者可以并存（谁跑谁覆盖数据，内容一致）。建议：

- 迁到 FC 后，把 `.github/workflows/update.yml` 里的 `schedule` 注释掉，只保留 `workflow_dispatch` 手动触发
- 这样日常由 FC 跑，Actions 只在你手动点的时候作为备胎

## 排错

| 现象 | 原因与处理 |
|---|---|
| 日志出现 `浏览器不可用，已降级为静态抓取` | 正常。FC 精简环境未装 chromium，`adapter: browser` 的源自动改用静态抓取；国内 IP 访问国内站基本不受影响 |
| `push` 返回 401/403 | `GH_TOKEN` 过期或权限不足，需 Contents 读写 |
| `超过 100MB 上限` | 代码包异常，检查是否误把 `data/`、`output/` 打进去 |
| 函数超时 | 把超时提到 3600 秒；或临时减少 `config.yaml` 里的源数量排查 |
| 部分省级站仍失败 | 各省政务云有独立白名单，见网页「信息源状态」页的具体报错 |

## 成本

函数计算免费额度（每月约 40 万 CU·秒）远高于本用量。按 1024MB、每次 25 分钟、每天 1 次：

```
1 CU × 1500 秒 × 30 天 ≈ 4.5 万 CU·秒/月   → 约占免费额度 11%
```

超出免费额度后按量计费，本用量每月也在 1 元以内。仓库的 Actions 用量同理可降为 0。
