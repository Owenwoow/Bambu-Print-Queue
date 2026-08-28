---
name: git-branch
description: bpq 项目的 git 分支、提交、合并、发布规则。当需要新建分支、给子 agent 开 worktree、合并改动、打 tag 发版、清理分支、或不确定某个改动该落在哪条分支上时使用。也用于回答「这个改动该开什么分支」「怎么合进 main」「怎么发版」。
---

# bpq 分支模型：主干开发，不用 git-flow

本项目不用 git-flow 那套 develop/release/hotfix 长期分支，只有一条长期分支 `main`，
其余都是短命分支，合完即删。选错分支模型会让分支列表越滚越乱，历史考古困难——这是
本项目真实吃过的亏（见文末「历史特殊情况」）。

## 分支一览

| 分支 | 生命周期 | 规则 |
|---|---|---|
| `main` | 永久，唯一长期分支 | 永远可发布：CI 绿、能打包、能起 daemon |
| `feat/*` `fix/*` `docs/*` `chore/*` `refactor/*` `test/*` | 短命，几小时到几天 | 从 main 切，合完即删；前缀与 Conventional Commits 的 type 对齐 |
| `agent/*` | 极短命，一次任务卡 | 子 agent 的 worktree 分支 |
| `release/0.N.x` | 按需，平时不存在 | 只在需要给已发布的旧版打补丁时才建 |
| `archive/*`（tag，非分支） | 永久 | 停更分支归档成 tag 后删分支，历史不丢、分支列表干净 |

分支名里的 scope 用项目既有的：`scheduler` / `web` / `transport` / `daemon` / `store`。
例如：`feat/scheduler-重试上限`、`fix/web-上传进度条卡死`。

## agent 分支规则（本项目最关键的一条）

子 agent 的分支/worktree 由**调度者**在派发任务前统一管理，不能交给工具默认行为：

- **命名 `agent/<任务卡短名>`，由调度者在派发前指定**，禁止用工具默认的
  `worktree-agent-<hash>`——那种名字合进 main 后回溯不出是哪张任务卡对应的改动。
  例如 `agent/fix-web-上传进度条`，不要 `agent/a3f9c2`。
- 每个子 agent 一个 worktree，互不踩，互不共享工作区。
- **合并一律 squash**，由调度者重写成一条符合 Conventional Commits 的中文提交信息，
  任务卡边界写进 commit body。理由：子 agent 的中间提交多是「先加个函数」「修个
  lint」这类噪声，`--no-ff` 保留下来只污染 main 的历史。
- **必须调度者亲自读 diff、跑一次关键命令验证通过才合**——对应 CLAUDE.md「多 Agent
  协作模式」流程第 5 步，不能只凭子 agent 的自我总结判断是否完成。
- 合完立刻删分支 + 删 worktree，不留过夜。
- agent 产生的临时文件（抓包、中间报告、临时脚本）一律走 scratchpad，不进仓库；真机
  报文含 SERIAL / wifi SSID，`docs/samples/` 已 gitignore，不得破例把这类文件塞进
  agent 分支里绕过。

## 合并规则

- **自己的分支**：本地 `git rebase main` 保持线性，然后快进或 squash；**不在已推送的
  公共分支上 rebase**（会让协作者的本地历史和远程分叉，重写别人已拉取的历史）。
- **外部 PR**：GitHub 网页上用 Squash and merge，标题由维护者按 Conventional Commits
  重写。合并前对照 CONTRIBUTING 的红线过一遍，尤其「触发前必须完全静默」和「不得
  另建 MQTT 连接」这两条最容易被外部贡献者无意中破坏。
- 保持线性历史，**不要 merge commit**。
- main 的分支保护（要求 CI 通过、禁止强推、禁止删除）在 GitHub 网页设置里配置，
  命令行做不了，别在这上面浪费时间找命令。

## 发布规则（发布 = 在 main 上打 tag）

1. 更新 CHANGELOG：把 `## [Unreleased]` 改成 `## [0.4.0] — YYYY-MM-DD`。
2. 同步版本号——当前有两处真源：`pyproject.toml` 的 `[project].version` 和
   `src/bpq/__init__.py` 的 `__version__`，两处都要改，长期看建议收敛成一处：
   `pyproject.toml` 写 `dynamic = ["version"]`，配 `[tool.hatch.version]` 的
   `path = "src/bpq/__init__.py"`（本项目 build-backend 是 hatchling，不是
   setuptools），以代码里的 `__version__` 为唯一真源。
   **不要反过来让 `__init__.py` 去读 `importlib.metadata`**——本项目用 PyInstaller
   打单文件，运行时读包元数据正是 v0.3 踩过 `pkg_resources` 那个坑的同一类问题。
3. 提交：`chore(release): 发布 0.4.0`，推到 main。
4. **等 CI 绿了再打 tag**：
   ```
   git tag -a v0.4.0 -m "v0.4.0"
   git push origin v0.4.0
   ```

**必须先推 main 再打 tag**：`.github/workflows/release.yml` 只 checkout tag 那个提交，
tag 若领先 main（本地打了 tag 但 main 还没推上去），Release 产物会和主干代码对不上，
发出去的二进制和公开仓库看到的源码不一致。

预发布用 `v0.4.0-rc.1`。

**已知坑（待修，写在这里防止踩第二次）**：`release.yml` 里 tag glob 是 `v*.*.*`，这个
glob **也会**匹配 `v0.4.0-rc.1` 这种预发布 tag；同时 docker 构建那步的
`type=raw,value=latest` 是无条件生效的，没有排除 rc 的判断。结果是打一个 rc tag 也会
把 `latest` 镜像顶掉，让还在验证阶段的版本变成生产环境拉到的「最新版」。修复前，打
rc tag 之后要手动检查一下 `latest` 有没有被误更新。

## 版本语义（0.x 阶段）

- **升 MINOR**：`config.toml` 字段不兼容、SQLite `SCHEMA_VERSION` 需要不可逆迁移、
  HTTP API 路径或字段变更。
- **升 PATCH**：WebUI 视觉调整、内部重构、bug 修复。

0.x 阶段不区分 MAJOR，破坏性变更也走 MINOR。

## 本仓库的历史特殊情况

这部分记录不是规则，是给后人（包括未来的调度者）看的背景，不写清楚会以为分支布局
有问题：

- **`main` 是为公开发布重做的孤儿干净历史**，只有 6 个提交，不含 v0.1 → v0.3 的完整
  开发过程。这是有意为之的重写，不是历史丢失。
- 真实开发史归档在两个 tag 上：
  - `archive/dev-20260825`（50 提交，v0.3 WebUI 那条线）
  - `archive/master-20260825`（14 提交，v0.1-v0.3 主线）
  需要考古某个决策的来龙去脉时，用 `git log archive/master-20260825` 之类的命令去翻，
  不要以为 main 的 6 个提交就是全部历史。
- `backup/main-before-rewrite`（a2f64d0）是 main 被 rebase 前的旧历史备份。远程
  `main` 与 `v0.3.0` tag 现在都已指向重写后的 e74851b，本地与远程一致，**不需要再
  强推**。这个备份是旧 SHA 那条线的唯一副本，确认不需要回退之前先留着；真要清理，
  按下面「归档一条停更分支成 tag」的做法归档，不要直接删。
- 涉及重写已推送历史的操作（rebase 已推送的提交、`push --force`、移动已发布的 tag）
  **必须由用户本人决定，agent 不得自行执行**，哪怕看起来「本地更对」。

## 常用命令速查

**开一个短命功能分支**
```
git checkout main
git pull
git checkout -b feat/scheduler-重试上限
```

**给子 agent 开 worktree（调度者在派发任务前执行）**
```
git worktree add ../bpq-agent-fix-web-上传进度条 -b agent/fix-web-上传进度条 main
```
派发给子 agent 时把这个 worktree 的路径和分支名一起带进任务卡提示词。

**squash 合并 agent 分支（调度者验证通过后执行）**
```
git checkout main
git merge --squash agent/fix-web-上传进度条
git commit -m "$(cat <<'EOF'
fix(web): 修上传进度条在大文件下卡死的问题

任务卡边界：只改 web/src/components/UploadProgress.tsx 里的进度计算逻辑，
不涉及后端 API。

EOF
)"
```

**合并后清理分支和 worktree**
```
git branch -d agent/fix-web-上传进度条
git worktree remove ../bpq-agent-fix-web-上传进度条
```

**归档一条停更分支成 tag**
```
git tag -a archive/old-feature-20260828 old-feature-branch -m "归档：停更，改动已过时"
git push origin archive/old-feature-20260828
git branch -d old-feature-branch
git push origin --delete old-feature-branch
```

**发版**
```
# 1. 改 CHANGELOG.md 的 [Unreleased] -> [0.4.0] — 2026-08-28
# 2. 同步版本号
#    pyproject.toml: version = "0.4.0"
#    src/bpq/__init__.py: __version__ = "0.4.0"
git add CHANGELOG.md pyproject.toml src/bpq/__init__.py
git commit -m "chore(release): 发布 0.4.0"
git push origin main
# 3. 等 GitHub Actions CI 绿了再执行下面两行
git tag -a v0.4.0 -m "v0.4.0"
git push origin v0.4.0
```
