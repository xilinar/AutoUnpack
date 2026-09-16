# AutoUnpack —— 跨平台智能解压工具 🐋📦

AutoUnpack 是一款基于 Python 开发的轻量级压缩包解压工具。它拥有图形化拖拽界面和命令行双模式，专为解决日文、中文等非 UTF-8 编码导致的文件乱码问题而生。

## ✨ 核心特性
- 🖱️ **图形化拖拽**：双击运行 EXE，把压缩包拖进窗口，选好编码点一下就行。
- 🌍 **跨平台兼容**：完美支持 Windows / Linux / macOS。
- 📦 **格式支持广泛**：支持 zip / tar / tar.gz / tar.bz2 / tar.xz / 7z / rar。
- 📝 **混合编码回退**：指定主编码（如 Shift-JIS）后，遇到个别 UTF-8 文件会自动回退解码，绝不乱码。
- 🛡️ **安全防护**：自带 Zip Slip 路径穿越防护，拒绝恶意压缩包越界写入。

## 📌 开发说明（AI 透明化声明）
本项目由作者主导架构设计与需求定义，部分代码与文档在 DeepSeek Harness 等 AI 工具辅助下生成。所有代码均已由作者本人审查、测试，并对其安全性与稳定性承担全部责任。

## 🚀 快速开始

### 1. 环境准备
需要 Python 3.8 或更高版本。

### 2. 安装依赖
在终端中执行以下命令：
pip install -r requirements.txt

### 3. 运行方式
**图形界面（推荐）：**
在终端中执行：
python auto_unpack_gui.py
拖入压缩包 -> 选择文件名编码和文本编码 -> 点击“开始解压”。

**命令行：**
在终端中执行：
python auto_unpack.py "日文资料.zip" --name-encoding shift_jis --text-encoding shift_jis --verbose
（解压日文压缩包，文件名和文本均使用 Shift-JIS）

python auto_unpack.py "资料打包.zip" --name-encoding gbk --text-encoding gbk
（解压中文老压缩包，使用 GBK）

python auto_unpack.py "D:\MMD" --batch --name-encoding shift_jis --text-encoding shift_jis
（批量解压目录下所有日文包）

### 4. 打包为 EXE（可选）
如果你想把工具分享给没有 Python 环境的朋友，可以使用 PyInstaller 打包：
在终端中执行：
pyinstaller -F -w --name "AutoUnpack" --collect-all tkinterdnd2 auto_unpack_gui.py
注意：打包时 auto_unpack.py 与 auto_unpack_gui.py 必须在同一目录。生成的 EXE 在 dist 文件夹内。

## 🖼️ 界面预览
[<img width="1172" height="918" alt="image" src="https://github.com/user-attachments/assets/df06ad37-b62c-4626-b96b-3141bdc463df" />
]

## 📄 开源协议
本项目采用 [MIT License](LICENSE) 协议开源。

## ⚠️ 免责声明
本工具仅用于解压个人合法获取的压缩文件。请勿使用本工具解压来源不明的文件。
作者不对因使用本工具解压并运行恶意文件而造成的任何系统损害承担责任。
