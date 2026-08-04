## 多端切换与协同开发极简指南

---

## 准备工作（仅需检查一次）

确认项目根目录下已创建 `.gitignore` 文件，且包含以下内容（防止数据库、缓存和环境文件上传造成冲突）：

```text
*.db
*.sqlite3
venv/
.venv/
__pycache__/
.env

## 一、首次配置（一台电脑仅做一次）

场景 A：第一台电脑（将本地项目传上 GitHub）
在 GitHub 上创建一个全新的空白仓库，复制仓库链接。
在本地项目终端（PowerShell / Terminal）依次运行以下命令
'''
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin [https://https://github.com/schwu06/risk-intel-system](https://github.com/schwu06/risk-intel-system)
git push -u origin main
'''

场景 B：第二台/新电脑（从 GitHub 拉取项目）
打开终端，进入你想保存项目的目录，运行：
'''
git clone [https://github.com/schwu06/risk-intel-system](https://github.com/schwu06/risk-intel-system)
cd risk-intel-system
'''
新建并激活Python虚拟环境，安装依赖：
'''
python -m venv venv
# Windows 运行:
.\venv\Scripts\activate
# Mac/Linux 运行:
source venv/bin/activate

pip install -r requirements.txt
'''

## 二、日常开发循环
项目开始前，打开cursor终端，运行：
'''
git pull
'''

结束工作后，在终端依次运行：
'''
git add .
git commit -m "记录这次改了什么（例如：更新主体评估接口）"
git push
'''


