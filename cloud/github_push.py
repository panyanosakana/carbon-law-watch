#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 提交模块（纯 urllib，无需 git 二进制）
============================================
用 Git Data API 一次提交多个文件，只提交内容有变化的文件。

所需环境变量：
  GH_TOKEN   细粒度令牌（仅需目标仓库 Contents: Read and write）
  GH_REPO    owner/repo
  GH_BRANCH  默认 main
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def _req(method: str, path: str, token: str, body=None, timeout=30):
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "carbon-law-bot")
    if data:
        req.add_header("Content-Type", "application/json")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"{method} {path} -> {e.code} {detail}")
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def _blob_sha(content: bytes) -> str:
    """Git blob 哈希（与远端比较，判断文件是否变化）。"""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(content))
    h.update(content)
    return h.hexdigest()


def commit_files(files: dict, message: str, token: str = "", repo: str = "",
                 branch: str = "") -> dict:
    """files: {仓库内路径: 本地绝对路径 或 bytes}
    返回 {"committed": bool, "changed": [...], "commit": sha}
    """
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    repo = repo or os.environ.get("GH_REPO", "")
    branch = branch or os.environ.get("GH_BRANCH", "main")
    if not token or not repo:
        raise RuntimeError("缺少 GH_TOKEN 或 GH_REPO")

    ref = _req("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)
    head = ref["object"]["sha"]
    base_tree = _req("GET", f"/repos/{repo}/git/commits/{head}", token)["tree"]["sha"]
    tree = _req("GET", f"/repos/{repo}/git/trees/{base_tree}?recursive=1", token)
    remote = {n["path"]: n.get("sha", "") for n in tree.get("tree", []) if n["type"] == "blob"}

    pending, changed = [], []
    for path, src in files.items():
        content = src if isinstance(src, bytes) else open(src, "rb").read()
        sha = _blob_sha(content)
        if remote.get(path) == sha:
            continue
        blob = _req("POST", f"/repos/{repo}/git/blobs", token, {
            "content": base64.b64encode(content).decode("ascii"), "encoding": "base64"})
        pending.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        changed.append(f"{path} ({len(content) // 1024} KB)")

    if not pending:
        return {"committed": False, "changed": [], "commit": head, "reason": "内容无变化"}

    new_tree = _req("POST", f"/repos/{repo}/git/trees", token,
                    {"base_tree": base_tree, "tree": pending})["sha"]
    new_commit = _req("POST", f"/repos/{repo}/git/commits", token, {
        "message": message, "tree": new_tree, "parents": [head]})["sha"]
    _req("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token, {"sha": new_commit, "force": False})
    return {"committed": True, "changed": changed, "commit": new_commit}


if __name__ == "__main__":
    print(json.dumps(commit_files({"README.md": "README.md"}, "test"), ensure_ascii=False))
