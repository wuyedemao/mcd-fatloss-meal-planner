# 一键初始化并推送到 GitHub（PowerShell 版）
#
# 用法（在项目目录下）：
#   .\init_repo.ps1 -UserName 你的GitHub用户名
#
# 前置：
#   1. 先在 https://github.com/new 建一个**空的公开仓库**，名字默认 mcd-fatloss-meal-planner
#      不要勾选 Add README / .gitignore / license
#   2. 本机已登录 GitHub（首次 push 会弹出浏览器授权）

param(
    [Parameter(Mandatory = $true)][string]$UserName,
    [string]$RepoName = "mcd-fatloss-meal-planner"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> 检查必备文件"
foreach ($f in @("README.md", "CONTEST_DECLARATION.md", "MCP_INTEGRATION.md", "mcp-config.example.json", "workbuddy.md", "SKILL.md")) {
    if (-not (Test-Path $f)) { Write-Host "[缺失] $f" -ForegroundColor Red; exit 1 }
    Write-Host "  ok  $f"
}

Write-Host "==> 自测（离线，不需要 Token）"
python scripts/selftest.py
if ($LASTEXITCODE -ne 0) { Write-Host "自测未通过，已中止" -ForegroundColor Red; exit 1 }

Write-Host "==> 初始化 git"
if (-not (Test-Path ".git")) {
    git init -q
    git branch -M main
}

git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git -c user.name="$UserName" -c user.email="$UserName@users.noreply.github.com" `
        commit -q -m "feat: 麦麦减脂配餐助手 - 基于麦当劳 MCP 的减脂配餐 Skill"
} else {
    Write-Host "没有需要提交的改动"
}

$remote = "https://github.com/$UserName/$RepoName.git"
$existing = git remote 2>$null
if ($existing -contains "origin") { git remote set-url origin $remote } else { git remote add origin $remote }

Write-Host "==> 推送到 $remote"
git push -u origin main

Write-Host ""
Write-Host "完成：https://github.com/$UserName/$RepoName" -ForegroundColor Green
Write-Host "下一步：去 https://github.com/M-China/mcd-developer-innovation-challenge/issues/new 提交报名（正文见工作区的 报名提交.md）"
