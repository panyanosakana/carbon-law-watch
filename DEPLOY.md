# 部署到 GitHub（自动更新网站 + Excel）

整个过程约 5 分钟，全部免费。部署完成后：**每天北京时间 08:00 自动采集 → 网站自动更新 → 仓库里自动存一份 Excel**，不消耗任何 WorkBuddy 积分。

---

## 前置：准备一个 GitHub 账号和仓库

1. 登录 [github.com](https://github.com)，右上角 **+** → **New repository**
2. 填写：
   - **Repository name**：`carbon-law-watch`（可自定义）
   - **Visibility**：选 **Public**（推荐，公开仓库 Actions 不限时长）
   - **不要**勾选 Add README / .gitignore / license（保持空仓库）
3. 点 **Create repository**，页面会给出仓库地址，形如：
   `https://github.com/你的用户名/carbon-law-watch.git`

---

## 方式 A：命令行推送（推荐，最稳）

本目录已经初始化好 git 并提交过一次，你只需加远程地址并 push。

在终端执行（把 `你的用户名` 换成实际账号）：

```bash
cd /path/to/carbon-law-watch
git remote add origin https://github.com/你的用户名/carbon-law-watch.git
git branch -M main
git push -u origin main
```

> **首次 push 需要令牌**：GitHub 已不支持密码。去
> Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token
> 勾选 **repo** 权限，生成后复制，push 时用户名填 GitHub 账号、密码粘贴这个 token。

如果本机装了 gh CLI，更省事：

```bash
gh auth login          # 按提示选 GitHub.com → HTTPS → 浏览器授权
gh repo create carbon-law-watch --public --source=. --remote=origin --push
```

---

## 方式 B：网页上传（不用命令行）

1. 在仓库页面点 **Add file** → **Upload files**
2. 把解压后的**所有文件和文件夹**（含隐藏的 `.github` 目录）一起拖进上传区
3. 底部填提交说明，点 **Commit changes**

⚠️ 网页上传的两个坑：

- **`.github` 是隐藏目录**，拖拽时如果没带上，定时任务就不会存在。上传完成后刷新仓库首页，必须能看到 `.github/workflows/update.yml` 这个文件；没有的话，用 **Add file → Create new file**，文件名填 `.github/workflows/update.yml`，把本地该文件内容粘进去提交。
- 不要只上传外层文件夹，要确保 `config.yaml`、`collector/`、`web/` 直接位于仓库根目录。

---

## 关键设置（务必做，否则跑不起来）

### 1. 打开 Actions 写权限

仓库 → **Settings** → 左侧 **Actions** → **General** → 拉到最底 **Workflow permissions**
→ 选 **Read and write permissions** → **Save**

> 不设置的话，采集能跑完但 `git push` 会 403，网站永远不更新。

### 2. 开启 GitHub Pages（网站上线）

仓库 → **Settings** → 左侧 **Pages**：

- **Source**：`Deploy from a branch`
- **Branch**：`main`，文件夹选 **`/web`**（注意不是根目录）
- 点 **Save**

等 1–2 分钟，页面顶部会显示网址：
`https://你的用户名.github.io/carbon-law-watch/`

### 3. 手动跑一次验证

仓库 → **Actions** 标签 → 左侧 `carbon-law-update` → 右侧 **Run workflow** → **Run workflow**

约 15–25 分钟后跑完，点开这次运行，最后应该看到类似日志：

```
[done] 源 56/59 成功 | 当月 13 条 | 领域覆盖 12/27 | 部委有独立发文 4/8
[done] Excel: 双碳法规汇总-2026-11.xlsx
```

跑完后回仓库首页，会看到机器人自动提交了一条 `法规更新 2026-11-14`，`web/data/regulations.json` 已更新，网站随之刷新。

---

## 部署后的日常

| 事项 | 说明 |
|---|---|
| 自动更新时间 | 每天 08:00（北京时间）。GitHub 定时任务可能延迟 10–30 分钟，属正常 |
| 手动立即更新 | Actions → carbon-law-update → Run workflow |
| 网站地址 | `https://你的用户名.github.io/仓库名/` |
| Excel 下载 | 仓库 `output/` 目录直接下载最新 xlsx；或 Actions 运行的 Artifacts 里下载 |
| 修改采集范围 | 改 `config.yaml`（增删信息源、关键词、排除词）→ 提交即可，下次运行生效 |
| 改抓取时间 | 改 `.github/workflows/update.yml` 里的 `cron: "0 0 * * *"`（UTC 时间，北京时间减 8 小时） |

---

## 零积分说明

- 定时器是 **GitHub 的服务器**，与本对话、WorkBuddy 积分体系无关，不会扣任何积分
- 没有配置任何 LLM API Key，要点是原文摘抄，**不产生模型调用费用**
- 公开仓库的 Actions 完全免费，本项目每天一次、每次约 20 分钟，远在免费额度内
- 最后记得：进 **WorkBuddy → 设置 / 自动化 → 找到原来的「法规收集」任务 → 关闭或删除**，避免继续扣积分

---

## 常见问题

**Q：Actions 报 permission denied / 403**
→ 没做上面「关键设置 1」，去开 Read and write permissions。

**Q：网站打开是 404**
→ Pages 分支文件夹要选 `/web` 而不是 `/`；刚开启时需等 1–2 分钟构建。

**Q：网站显示"数据加载失败"**
→ 检查仓库里 `web/data/regulations.json` 是否存在。若只是本地打开 `web/index.html`，浏览器会因跨域拦截 fetch，这是正常的，请改用根目录的 `双碳法规监测-单文件版.html`。

**Q：抓取成功率比本地低**
→ GitHub 美国机房访问部分地方政府站点可能超时，属网络差异。失败源会在网页「信息源状态」标签页列出原因，不影响整体运行。

**Q：想抓取频率改成每天两次**
→ 改 cron 为 `0 0,12 * * *`（UTC 00:00 与 12:00，即北京时间 08:00 与 20:00）。
