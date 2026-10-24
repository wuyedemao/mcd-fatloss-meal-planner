#!/usr/bin/env bash
# 一键初始化并推送到 GitHub。
#
# 用法：
#   bash init_repo.sh <你的GitHub用户名> [仓库名]
#
# 前置：
#   1. 先在 https://github.com/new 建一个**空的公开仓库**，名字默认 mcd-fatloss-meal-planner
#      不要勾选 Add README / .gitignore / license，否则会多出一条无关提交
#   2. 本机已登录 GitHub（首次 push 会弹出浏览器授权）

set -euo pipefail

USER_NAME="${1:-}"
REPO_NAME="${2:-mcd-fatloss-meal-planner}"

if [ -z "$USER_NAME" ]; then
  echo "用法: bash init_repo.sh <你的GitHub用户名> [仓库名]"
  exit 1
fi

cd "$(dirname "$0")"

echo "==> 检查必备文件"
for f in README.md CONTEST_DECLARATION.md MCP_INTEGRATION.md mcp-config.example.json workbuddy.md SKILL.md; do
  if [ ! -f "$f" ]; then
    echo "[缺失] $f"
    exit 1
  fi
  echo "  ok  $f"
done

echo "==> 自测（离线，不需要 Token）"
python scripts/selftest.py || { echo "自测未通过，已中止"; exit 1; }

echo "==> 初始化 git"
if [ ! -d .git ]; then
  git init -q
  git branch -M main
fi

git add -A
if git diff --cached --quiet; then
  echo "没有需要提交的改动"
else
  git -c user.name="$USER_NAME" -c user.email="$USER_NAME@users.noreply.github.com" \
      commit -q -m "feat: 麦麦减脂配餐助手 - 基于麦当劳 MCP 的减脂配餐 Skill"
fi

REMOTE="https://github.com/${USER_NAME}/${REPO_NAME}.git"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE"
else
  git remote add origin "$REMOTE"
fi

echo "==> 推送到 $REMOTE"
git push -u origin main

echo
echo "完成：https://github.com/${USER_NAME}/${REPO_NAME}"
echo "下一步：去 https://github.com/M-China/mcd-developer-innovation-challenge/issues/new 提交报名（正文见工作区的 报名提交.md）"
