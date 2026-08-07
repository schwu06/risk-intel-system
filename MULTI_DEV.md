## 多端切换与协同开发极简指南

---

## 准备工作（仅需检查一次）

确认项目根目录下已创建 `.gitignore` 文件，且至少包含以下内容（防止数据库、缓存和环境文件上传造成冲突）：

```text
venv/
.venv/
__pycache__/
*.py[cod]
*.db
*.db-shm
*.db-wal
*.sqlite3
data/uploads/
data/exports/
.env
.idea/
.vscode/
```

## 一、首次配置（一台电脑仅做一次）

### 场景 A：第一台电脑（将本地项目传到 GitHub）

在 GitHub 上创建一个全新的空白仓库，复制仓库链接。  
在本地项目终端（PowerShell / Terminal）依次运行：

```powershell
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/schwu06/risk-intel-system.git
git push -u origin main
```

### 场景 B：第二台/新电脑（从 GitHub 拉取项目）

打开终端，进入你想保存项目的目录，运行：

```powershell
git clone https://github.com/schwu06/risk-intel-system.git
cd risk-intel-system
```

新建并激活 Python 虚拟环境，安装依赖：

```powershell
python -m venv .venv
# Windows:
.\.venv\Scripts\Activate.ps1
# Mac/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env
```

按需填写 `.env` 中的密钥后启动服务（详见 README）。

## 二、日常开发循环

项目开始前，打开 Cursor 终端，运行：

```powershell
git pull
```

结束工作后，在终端依次运行：

```powershell
git diff --check
git add .
git status
git commit -m "记录这次改了什么（例如：更新主体评估接口）"
git push
```

提交前同步更新 `CHANGELOG_20260805.md`；如果启动方式、依赖、数据库结构、API 或用户操作流程发生变化，同时更新 `README.md`。确认 `git status` 中不包含 `.env`、数据库、上传文件、导出文件或运行日志。
