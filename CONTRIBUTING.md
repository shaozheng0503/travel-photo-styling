# 贡献指南

欢迎提交 PR。本仓库保持**零第三方依赖**（Python 3.8+ 标准库），这是硬约束。

## 三条主线，任选其一

### 1. 新增生图 Provider（最欢迎）

优先走**纯配置**路线：在 `config/providers.json` 里加一段，抄 `myapi` 改改即可，
不用写代码。三种响应模式覆盖绝大多数服务：

| 模式 | 适用 |
|---|---|
| `sync_url` | POST 直接返回图片 URL |
| `sync_b64` | POST 直接返回 base64 |
| `poll_task` | POST 返回 task_id → 轮询另一端点 |

只有当 API 需要签名、特殊鉴权刷新或非标准流程时，才写 Python 类：
新建 `providers/your_provider.py`，继承 `providers/base.py` 的 `ImageProvider`，
实现 `submit / poll / download`，然后在 `providers/__init__.py` 的
`BUILTIN_PROVIDERS` 里注册。参考 `providers/imagifly.py`（约 200 行完整实现）。

### 2. 新增提示词模板

- 放 `prompts/t2i/`（文生图）或 `prompts/i2i/`（图生图），文件名 `编号_风格_类型.txt`
- **纯文本**：CLI 会把整个文件渲染成一行提示词，不要写 Markdown 标题、列表符号、
  注释行——它们会被当成提示词的一部分喂给模型
- 建议在开头明确构图（如「3:4 竖版，上下 1:1」），结尾写清「避免……」的负面约束
- 同步更新 `prompts/README.md` 总览表和 `风格速查表.md`
- 提交前请真的跑一遍生成，并在 PR 里贴出效果图

### 3. 报告问题 / 改进文档

Issue 里请带上：Python 版本、操作系统、完整报错、`--check` 输出。

## 提交前自检

```bash
python tests/test_offline.py     # 必须全绿（53 用例，纯离线）
python main.py --list-providers  # CLI 冒烟
```

新增逻辑请补进 `tests/test_offline.py`，保持零网络、零花费。

## 不要提交

Cookie、API Key、真实照片、运行产物（`images/`、`done.txt`、`results.jsonl`、
`failed.jsonl`、`run.log`、`prompts.txt`）——已在 `.gitignore` 里。
改配置请用 `config/providers.local.json` 或环境变量。

## 许可证

代码 MIT；`prompts/` 下的提示词模板 CC BY 4.0（见 `LICENSE-PROMPTS.md`）。
提交 PR 即表示你同意按此许可发布你的贡献。
