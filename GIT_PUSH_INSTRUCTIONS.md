# 推送项目到 GitHub 的步骤

由于 `baselines/` 目录包含数万个文件导致 Git 操作超时，请按以下步骤手动操作：

## 方法 1：手动删除 baselines 后推送（推荐）

1. **手动删除 baselines 目录**：
   - 在文件资源管理器中打开项目文件夹
   - 删除 `baselines\firmament` 和 `baselines\mesos` 两个子目录
   - 保留其他文件

2. **打开 PowerShell 或 Git Bash**，进入项目目录：
   ```powershell
   cd "C:\Users\qiyan\Desktop\资源分配"
   ```

3. **删除旧的 Git 仓库并重新初始化**：
   ```bash
   Remove-Item -Recurse -Force .git
   git init
   ```

4. **添加远程仓库**：
   ```bash
   git remote add origin git@github.com:qiyan111/QY-.git
   ```

5. **添加所有文件**（现在应该很快，因为没有 baselines）：
   ```bash
   git add .
   ```

6. **提交**：
   ```bash
   git commit -m "Initial commit: Resource allocation scheduler with RL"
   ```

7. **推送到 GitHub**：
   ```bash
   git push -u origin master
   ```
   
   如果遇到分支名称问题，使用：
   ```bash
   git branch -M main
   git push -u origin main
   ```

## 方法 2：使用 Git Bash（更稳定）

如果 PowerShell 持续超时，使用 Git Bash：

1. 右键点击项目文件夹，选择 "Git Bash Here"
2. 执行以下命令：

```bash
# 删除 baselines 大目录
rm -rf baselines/firmament baselines/mesos

# 重新初始化
rm -rf .git
git init

# 配置远程仓库
git remote add origin git@github.com:qiyan111/QY-.git

# 添加文件
git add .

# 提交
git commit -m "Initial commit: Resource scheduler project"

# 推送
git push -u origin master
```

## 方法 3：使用 GitHub Desktop（最简单）

1. 下载安装 GitHub Desktop: https://desktop.github.com/
2. 打开 GitHub Desktop，选择 "Add Existing Repository"
3. 选择项目文件夹
4. 在右侧 Changes 列表中，取消勾选 `baselines/firmament` 和 `baselines/mesos`
5. 填写 Commit message，点击 "Commit to master"
6. 点击 "Push origin"

## 关于 baselines 目录的说明

`.gitignore` 文件已配置排除 `baselines/` 目录。

如果需要，可以在 GitHub 仓库的 README 中添加说明：
- Firmament 源码: https://github.com/camsas/firmament
- Mesos 源码: https://github.com/apache/mesos
- Python 实现已包含在 `tools/scheduler_frameworks/` 目录中

## 验证推送成功

推送完成后，访问：https://github.com/qiyan111/QY-

应该看到项目文件（不包含 baselines 大目录）。

---

## 如果所有方法都失败

项目文件太大可能超出 GitHub 免费版限制。考虑：
1. 使用 Git LFS（Large File Storage）
2. 将项目分成多个仓库
3. 使用 Gitee 或其他代码托管平台（中国境内速度更快）

