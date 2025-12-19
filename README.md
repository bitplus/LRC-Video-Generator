# LRC Video Generator (Web Version)

**一款强大而直观的工具，旨在将您的音乐、歌词和专辑封面无缝融合，创造出具有专业水准的卡拉OK或动态歌词视频。**

本版本已升级为 **Web 界面**，基于 **FastAPI** 和 **HTML5/CSS3** 构建，提供更现代、更美观且跨平台的使用体验。您无需安装 Qt 库，只需浏览器即可使用。

## ✨ 核心功能

- **现代 Web 界面**: 清爽的暗色主题设计，操作流畅，视觉体验极佳。
- **工程化管理**: 支持保存和加载项目 (`.json` 格式)，方便您随时中断和恢复工作。
- **实时效果预览**: 在最终渲染前，可随时预览视频在任意时间点的静态画面。
- **智能色彩提取**: 自动从您的封面图片中分析并提取出最佳的主色、辅色和描边色。
- **丰富的可定制动画**:
  - **背景动画**: 内置“静态模糊”、“动态渐变波浪”等多种背景效果。
  - **歌词动画**: 提供“淡入淡出”、“滚动列表”等多种精美的歌词展示动画。
  - **封面动画**: 可选择“静态展示”或模拟“黑胶唱片旋转”效果。
- **高性能视频编码**: 支持硬件加速 (NVIDIA, AMD, Intel)。

## 📋 环境依赖

1. **[FFmpeg](https://ffmpeg.org/download.html)**: **必需项**。
   - 请下载并安装 FFmpeg，并将其路径添加到系统环境变量中。
   - 或者在软件界面的“高级设置”中指定路径。

2. **Python**: 推荐使用 3.10 或更高版本。

3. **Python 库**:
   ```bash
   pip install -r requirements.txt
   ```
   (主要依赖: `fastapi`, `uvicorn`, `python-multipart`, `aiofiles`, `Pillow`, `scikit-learn`, `numpy`)

4. **字体文件**:
   - 请将您希望在视频中使用的字体文件（如 `.ttf` 或 `.otf` 格式）放入程序根目录下的 `font` 文件夹中。

## 🚀 快速开始

1. **启动程序**: 在命令行中运行 `app.py`：
   ```bash
   python app.py
   ```
   或者直接使用 uvicorn:
   ```bash
   uvicorn app:app --reload
   ```

2. **访问界面**: 程序启动后会自动在浏览器中打开 `http://127.0.0.1:8000`。

3. **使用流程**:
   - **上传文件**: 选择音频、封面、LRC文件。
   - **设置样式**: 调整动画、字体、颜色。
   - **预览**: 拖动滑块并点击“生成预览”。
   - **生成**: 点击“开始生成视频”，等待完成后下载。

## 📂 项目结构

```
LRC-Video-Generator/
├── app.py               # Web应用主入口 (FastAPI)
├── static/              # 前端资源
│   ├── index.html
│   ├── style.css
│   ├── script.js
├── video_processor.py   # 核心视频处理模块
├── animations.py        # 动画滤镜定义
├── lrc_parser.py        # LRC解析器
├── color_extractor.py   # 颜色提取
├── requirements.txt     # 依赖列表
└── font/                # 字体目录
```

## ⚠️ 注意事项

- 旧版 `main.py` (PySide6) 已不再维护，建议使用新的 Web 版本。
- 视频生成在后台进行，请勿关闭终端窗口。
