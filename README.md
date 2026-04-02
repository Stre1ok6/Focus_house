# Focus Compass

这是一个基于 Flask 的本地网页工具，用来分析一组学习截图是否持续服务于当前专注目标。

当前版本的实际能力是：

- 用户自由输入本次专注目标
- 批量上传截图并生成分析报告
- 支持 OCR、目标词抽取、DeepSeek 评分和时间窗修正
- 提供基础的 Session API，便于后续接实时截图流

## 当前项目结构

```text
app.py
focus_engine/
  __init__.py
  config.py
  deepseek_scoring.py
  goal_profiles.py
  models.py
  ocr.py
  pipeline.py
  scoring.py
  session.py
  utils.py
templates/
  index.html
static/
  styles.css
requirements.txt
run_focus_site.bat
测试.png
```

## 主要文件

- `app.py`
  Flask 入口，提供页面路由、批量分析接口和 Session API。
- `focus_engine/`
  核心分析逻辑，包括 OCR、目标理解、评分、汇总和实时会话管理。
- `templates/index.html`
  页面模板。
- `static/styles.css`
  页面样式。
- `测试.png`
  演示分析接口使用的本地样例图。

## 启动方式

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 配置 Tesseract OCR

默认会自动尝试常见 Windows 安装路径；如果你的安装路径不同，可以设置环境变量：

```bash
set TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

3. 如需启用 DeepSeek 评分，请在本地 `.env` 或系统环境变量中配置：

```bash
set DEEPSEEK_API_KEY=your_api_key
```

4. 启动项目

```bash
python app.py
```

也可以直接运行：

```bash
run_focus_site.bat
```

5. 打开浏览器访问：

`http://127.0.0.1:5000`

## 主要接口

- `POST /analyze`
  表单上传截图并渲染网页报告。
- `POST /api/analyze`
  返回 JSON 结果。
- `POST /api/session/start`
  开启一个实时分析会话。
- `POST /api/session/<session_id>/frame`
  追加一张截图到会话窗口。
- `GET /api/session/<session_id>`
  查看当前会话状态。
- `GET /api/analyze-demo`
  使用 `测试.png` 走一遍演示分析。

## 清理说明

项目已经去掉历史实验档案、重复网页文件、缓存目录和无运行时作用的规划文档。当前目录只保留运行这个网页应用所需的核心文件。

# 技术说明

## 总体改动概述
本次把原先的「Tesseract OCR + DeepSeek 文本评分」替换为「SiliconFlow 多模态（视觉）大模型一次性完成：读图提取文字 + 目标相关性量化评分」。
是否调用大模型：是。通过 SiliconFlow 的 OpenAI 兼容接口 `POST /chat/completions`，在 `messages` 中用 `image_url`（JPEG Base64 Data URL）传截图，并要求返回严格 JSON。
参考接口文档：https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions
---
## `focus_engine/siliconflow_vlm.py`（核心：VLM 读图与评分）
### 思路
1. 将输入截图缩放到 API 友好的尺寸上限，避免超大图导致请求失败或超时。
2. 编码为 JPEG，并构造 `data:image/jpeg;base64,...` 作为 `image_url`。
3. 构建 `system_prompt + user_text`，把：
   - 用户目标（raw_goal / normalized_goal）
   - 关键词/负向关键词（core/scene/support/semantic/negative keywords）
   - 截图文件名
   下发给模型。
4. 要求模型输出包含 `ocr_text/ocr_quality` 以及一套固定的评分 JSON 字段。
5. 对返回内容做鲁棒解析：
   - 优先把模型 `message.content` 当作纯 JSON 直接解析；
   - 解析失败则从内容中提取第一个完整 JSON 对象（括号计数 + 字符串转义处理），避免“未闭合字符串 / 输出夹带文本”导致崩溃。
6. 解析失败时（疑似输出被截断）可适当提高 `max_tokens` 重试一次。
### 用到的技术
- OpenCV：`cv2.imencode` 编码 JPEG、缩放 resize
- Python 标准库：`urllib.request` 发起 HTTP 请求、`json` 解析、`hashlib` 做摘要
- LRUCache：缓存重复帧/重复输入的评分结果，减少重复调用
- 鲁棒 JSON 提取：处理模型输出被截断或夹带非 JSON 文本的情况
### 是否调用大模型
- 是：调用 SiliconFlow 多模态大模型完成“读图 + OCR文本 + 评分”。
---
## `focus_engine/pipeline.py`（接入替换结果来源）
### 思路
- 将原有 `FastOCREngine + DeepSeekScorer` 链路替换为仅 `SiliconFlowVlmScorer`：
  - 唯一帧：调用 VLM 获取 `ocr_text/ocr_quality` 与所有评分分项
  - 重复帧：基于去重结果复用上一帧评分结果（只改 index/filename 等字段）
- 保持原有后处理逻辑接口不变，让 `scoring.py` 的时间窗修正仍能工作。
### 用到的技术
- `ThreadPoolExecutor` + `as_completed`：并发分析唯一帧
- 感知哈希去重：沿用 `average_hash/hash_distance`
### 是否调用大模型
- 间接调用：唯一帧时由 `siliconflow_vlm.py` 发起大模型请求。
---
## `focus_engine/config.py`（配置迁移）
### 思路
- 将 DeepSeek 配置迁移为 SiliconFlow 配置：
  - `SILICONFLOW_API_KEY`
  - `SILICONFLOW_BASE_URL`
  - `SILICONFLOW_MODEL`
  - `SILICONFLOW_TIMEOUT_SECONDS`
  - `SILICONFLOW_TEMPERATURE`
  - `SILICONFLOW_MAX_TOKENS`
  - `SILICONFLOW_ENABLE_THINKING`
### 是否调用大模型
- 否（仅提供配置/参数）。
---
## `focus_engine/ocr.py`（职责调整为“兼容层”）
### 思路
- 原本 OCR（Tesseract）流程迁移后，`ocr.py` 不再真正运行 Tesseract。
- 保留与图像相关的通用能力（解码、裁剪、缩放、哈希工具），并保留 `configure_tesseract()` 接口以兼容调用方。
- 现在 `configure_tesseract()` 用于表达 SiliconFlow 是否已配置（Key 是否存在）而非本地 OCR 就绪状态。
### 用到的技术
- OpenCV、numpy：解码/预处理/哈希相关功能
### 是否调用大模型
- 否（只做图像准备与兼容返回）。
---
## `focus_engine/scoring.py`（后处理兼容 siliconflow 来源）
### 思路
- 保留原有的时间窗一致性、最终 focus/status 规则等后处理逻辑。
- 兼容评分来源：
  - `scoring_source in {"deepseek", "siliconflow"}`
- 提示文案从 DeepSeek 替换为 SiliconFlow。
### 是否调用大模型
- 否（只基于输入的分数字段进行后处理）。
---
## `requirements.txt`（依赖调整）
- 移除了 `pytesseract`：因为 OCR 已迁移到多模态模型。
- 仍保留 `opencv-python`、`numpy` 等用于图像处理与哈希去重。
---
## `.env`（团队可复用的配置）
`.env` 已替换为 SiliconFlow 版（建议团队直接复制模板后填写 Key）。
关键变量：
- `SILICONFLOW_API_KEY`（必填）
- `SILICONFLOW_MODEL`（必须是控制台可用且支持视觉/多模态的模型 ID）
- `SILICONFLOW_ENABLE_THINKING=false`（建议默认 false 降低延迟；如需可改为 true，需模型支持）
- `SILICONFLOW_TIMEOUT_SECONDS`、`SILICONFLOW_MAX_TOKENS` 可按网络/模型速度调大
---
## 总结（便于后续追溯）
- 大模型调用位置：`focus_engine/siliconflow_vlm.py`
- 大模型调用参数与协议：OpenAI 兼容 `POST /chat/completions`，`messages` 中包含 `image_url`（data URL）
- 重复帧复用：`pipeline.py` 的感知哈希去重
- 最终判定/窗口平滑：`scoring.py` 的 `finalize_frame_scores`
