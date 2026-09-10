# 旅行照片配图 · Agent 使用说明

给 TRAE / Claude Code / Cursor / Codex 等 AI Agent 看的操作手册。
人类用户请读 [README.md](README.md)。

## 你的任务形态

用户会给：一批照片路径（或目录）+ 想要的风格（或让你选）。
你要产出：`prompts.txt` 队列 → 预检 → 确认成本 → 运行 → 汇报结果 + 产物路径。

## 标准流程（按顺序执行，别跳步）

```bash
# 1. 环境自检（不花钱）
python main.py --check

# 2. 模板入队：一张照片 × 一套风格 = 一条队列
python main.py --render i2i/03a_记忆贴纸_单层_I2I.txt --img "照片绝对路径.jpg" --add

# 3. 零成本预检三连
python main.py --validate     # 校验模型/比例/参考图
python main.py --estimate     # 估算成本
python main.py --dry-run      # 解析预演

# 4. 跑（高成本任务需 --confirm）
python main.py                # 或 python main.py 3（只跑前 3 条）
python main.py --confirm      # 单条成本 ≥5 时必须

# 5. 汇报
python main.py --summary
```

## 关键约定

- **路径用绝对路径**：`--img C:/Users/xxx/photo.jpg`（正斜杠）。相对路径按 cwd 解析，容易错。
- **一次一图一风格**：`--render` 一条命令只加一条；多条就跑多次 `--add`。
- **失败不慌**：失败的进 `failed.jsonl`，`--retry-failed` 一键重入队重跑。
- **成本意识**：跑批前必须 `--estimate`。`nano-banana-2` 等贵模型必须让用户确认。
- **T2I 模板要填占位符**：`--var COUNTRY=日本`，漏填会提醒。

## 风格怎么选（三问速判）

1. 有照片吗？没有 → `t2i/` 两套；有 → `i2i/`
2. 主体是什么？人+景 → 03/03a/03b；建筑 → 03/05/06；生活场景 → 04；器物静物 → 03c/05
3. 想要什么气质？治愈收藏 → 03a/03b/04；高级编辑 → 05/06；纪念感 → 03c；全家桶 → 03

完整适配矩阵见 `prompts/风格速查表.md`。

## 换生图服务

默认 `imagifly`。换服务：编辑 `config/providers.json`（纯配置，三种响应模式），
或队列行里加 `provider=xxx`。不需要改任何 Python 代码。

## 常见坑

- **模型名写错不报错**：某些服务会静默 fallback 并照常扣费。跑批前 `--list-models` 核对。
- **登录态看字段不看状态码**：部分服务 HTTP 200 但 `user: null` 其实是过期。
- **Windows 控制台中文**：本项目已强制 UTF-8 输出，别再手动 chcp。

## 别做的事

- 别在没跑 `--estimate` 的情况下直接跑整批
- 别把 Cookie / API Key 写进任何会被提交的文件
- 别猜测模板文件名，先 `ls prompts/i2i prompts/t2i`
