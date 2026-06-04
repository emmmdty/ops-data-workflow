# 原生内容投放清洗与复盘工作台

这是一个局域网内使用的 Streamlit 工作台，用于把多个渠道导出的 Excel、CSV 或 ZIP 统一清洗、去重、补齐并入库，再用于图表分析和手动 AI 复盘。

当前主流程固定为：

1. 上传同一周期的一个或多个原始文件。
2. 系统按周期和渠道保存原始文件；同周期同渠道再次上传时，可确认覆盖旧文件。
3. 系统生成一个统一清洗 workbook：`processed/{period_key}/{batch_id}/cleaned_channels.xlsx`。
4. 人工只审核重点内容：每渠道消耗 Top 20、单条消耗 >= 2000 元、高风险冲突、关键字段补齐失败。
5. 审核保存后，清洗 workbook、SQLite、图表和手动 AI 复盘输入同步刷新。
6. 总览页手动触发 AI 复盘报告生成。

页面模式下，`cleaned_channels.xlsx` 是主要业务交付物；`outputs/` 只保留给显式命令行导出，不作为网页默认产物。

## 启动

推荐：

```bash
uv run streamlit run app.py
```

如果已创建 `.venv`：

```bash
.venv/bin/streamlit run app.py
```

`.streamlit/config.toml` 已配置局域网监听。启动后同事可通过本机局域网 IP 访问，例如：

```text
http://本机局域网IP:8501
```

不要在同事正在使用时直接停止或替换现有服务；上线替换应另约窗口。

## 目录口径

- `data/months/`：月度原始源文件。
- `data/weeks/`：周度原始源文件。
- `data/reference/`：投稿台账、历史映射和人工参考表。
- `config/channel_profiles.yml`：渠道配置，默认覆盖小红书商业化、小红书市场部、抖音市场部、抖音商业化、B站市场部、B站商业化、微信市场部、微信商业化。
- `config/field_mapping.yml`：通用字段别名。
- `processed/{period_key}/{batch_id}/`：清洗产物和诊断文件。
- `.runtime/workflow.sqlite3`：页面批次、最终明细、图表和复盘输入。
- `outputs/`：命令行显式导出目录，网页主流程不依赖它。

`data/raw/`、旧 `archive/`、`output/playwright/` 不再作为网页运行入口。

## 上传规范

网页支持：

- 单个或多个 `.xlsx`、`.xls`、`.csv` 文件。
- ZIP 压缩包。
- 文件夹上传。

文件名应尽量包含渠道关键词，例如：

- `小红书商业化.xlsx`
- `小红书市场部-202605.xlsx`
- `抖音商业化.csv`
- `抖音市场部.xlsx`
- `B站.xlsx`
- `B站商业化.xlsx`
- `微信市场部.xlsx`
- `微信商业化.xlsx`

系统会按 `config/channel_profiles.yml` 识别渠道。无法识别的文件会作为新渠道保留，平台写为“其他”，后续再通过配置归并。

## 统一清洗 Workbook

每次成功生成页面数据时，会在 `processed/{period_key}/{batch_id}/` 下写出：

- `cleaned_channels.xlsx`：统一清洗 workbook，一个渠道一个 sheet。
- `cleaned.xlsx`：内部标准明细和导入诊断，供系统回放与审核同步使用。
- `period_manifest.json`：本次清洗清单。

`cleaned_channels.xlsx` 固定包含系统 sheet：

- `导入日志`
- `重复内容`
- `冲突项`
- `补齐来源`
- `审核记录`

每个渠道 sheet 先展示统一字段，再展示原始剩余字段。未映射的原始字段按原表头保留，例如原表里叫“7日付费成本”，清洗结果里也叫“7日付费成本”；已映射到统一字段的原始列不重复输出。

## 清洗和审核

清洗规则：

- 同渠道内优先按内容 ID 去重，其次按规范化链接，再按规范化标题。
- 消耗、曝光、点击、激活、付费会累加。
- 成本和率用合计值重新计算。
- 投稿台账、历史映射和平台公开信息会尽量补齐标题、链接、内容 ID、内容类型等字段。
- 平台公开信息补齐失败不会阻断清洗，会写入 `补齐来源` 和审核队列。

内容审核页只展示重点内容，不再把普通低风险 AI 分类结果全量交给人工。

## AI 复盘

上传和清洗不会自动生成 AI 报告。进入“总览”页后，点击手动 AI 复盘按钮才会调用模型生成报告。

可选环境变量：

```env
DEEPSEEK_API_KEY=你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

没有 key 时，数据清洗、图表和人工审核仍可正常使用。

## 常用命令

运行测试：

```bash
uv run python -m pytest -q
```

命令行生成页面数据：

```bash
uv run python main.py \
  --input data/weeks/20260518-20260524 \
  --period-start 2026-05-18 \
  --period-end 2026-05-24 \
  --processed-root processed \
  --db .runtime/workflow.sqlite3 \
  --ui-only
```

只看外部目录导入计划：

```bash
uv run python main.py --import-source ../data --dry-run --data-root data
```

## 维护说明

- 渠道新增或关键词调整：优先改 `config/channel_profiles.yml`。
- 通用字段别名调整：改 `config/field_mapping.yml`。
- 投稿台账或历史映射：放入 `data/reference/` 后重新生成对应周期。
- 审核保存后会重新生成当前批次结果，并刷新图表和手动 AI 复盘输入。

详细同事操作说明见 [docs/同事使用说明.md](docs/同事使用说明.md)。
