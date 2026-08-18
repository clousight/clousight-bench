# Clousight Bench — Agent 入口

本仓库是 Clousight Bench 开源测评核心。开发与贡献约定见 `README.md`。

## 仓库身份（强制）

- **GitHub 操作账号**：`clousight-dev`。`scripts/gitsync.sh` 会拒绝其它 `gh` 账号，并把本仓 `user.name` / `user.email` 写成 `Clousight` / `306954191+clousight-dev@users.noreply.github.com`（GitHub 按邮箱归属账号；个人 Gmail 会显示成个人 GitHub 用户）。
- 未走 gitsync 时，向 GitHub 提交、推送、创建 PR、Issue 或 Release 前先用 `gh auth status` 确认；不是该账号时执行 `gh auth switch --user clousight-dev`。
- 提交继续遵守仓库 DCO 要求（`git commit -s`）。`gitsync commit` 会自动加 `-s`。
