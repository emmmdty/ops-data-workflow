# 原生内容投放清洗与复盘工作台

这是局域网内使用的 Streamlit 工作台，用于把各渠道投放数据统一清洗入库，再对抖音、小红书、B站的高价值内容做素材缓存和复盘。

当前产品入口只保留六个页面：

- `总览`：查看本周期总数据、渠道总数据、清洗状态、复盘状态。
- `上传清洗`：上传周/月/季度/年度数据，或根据本地月度数据生成季度/年度汇总。
- `高价值内容复盘`：维护价值权重，筛选 Top 素材，复用 harvester 素材缓存并生成类型复盘。
- `本地总表`：查看和同步本地 `content_assets`。
- `周期数据`：查看本周期明细、渠道总数据、素材缓存记录和复盘结果。
- `历史趋势`：查看周、月、季度、年度核心指标趋势。

线上飞书只读。本项目会读取飞书台账并更新本地 SQLite，不会自动写回线上飞书。

## 启动

同事日常使用推荐直接双击项目根目录的启动文件：

- macOS：`启动局域网.command`
- Windows：`启动局域网.cmd`

启动脚本会检查环境并启动 Streamlit。默认从 `8501` 端口启动；如果端口被占用，会自动尝试后续端口。以启动窗口最后打印的地址为准。

命令行备用启动方式：

```bash
uv run streamlit run app.py
```

## 主流程

1. 进入 `上传清洗`，上传同一周期的 Excel、CSV、ZIP 或文件夹。
2. 选择周期维度：周、月、季度或年度。
3. 系统标准化字段、识别单行渠道总数据、同渠道内去重聚合、读取飞书台账并回填缺失字段；如果抖音、小红书或 B站最新投稿时间超过 3 天未更新，会先提示人工确认。
4. 清洗完成后，本周期明细进入 `content_performance_items`，单行渠道总数据进入 `period_channel_totals`。
5. 进入 `高价值内容复盘`，填写激活权重和付费权重；保存后会作为默认值复用，直到下次更新。
6. 对抖音、小红书、B站高价值池复用 harvester 每日缓存，缺失素材再补采。
7. 生成复盘后，素材结果写入 `multimodal_recap_items`，类型聚合写入 `type_recap_items`，新素材写入本地总表和 manifest，避免二次爬取。

如果某渠道 Excel 只有一行数据，系统视为渠道总数据，只进入总览，不进入素材明细、去重、匹配或高价值复盘。

## 数据口径

清洗后素材明细保留核心字段：

- 周期、渠道、平台 ID、账号、标题、tag 词。
- 抖音/小红书一级类型、抖音/小红书二级类型、B站内容类型。
- 链接、消耗、曝光、激活数、付费数、激活成本、付费成本。

平台 ID 规则：

- 抖音使用标准 URL 作品 ID；分享链接会尽量解析为标准 URL 和作品字段。
- B站使用 BV 号。
- 小红书使用笔记 ID。

渠道内重复素材按稳定内容身份合并，指标值相加，成本重新计算。

## 高价值复盘口径

只分析抖音、小红书、B站：

- 抖音：按渠道消耗 Top20、曝光 Top20。
- 小红书/B站：按渠道消耗 Top10、曝光 Top10。
- 任一平台单素材消耗 > 2000 或曝光 > 100000，也进入高价值池。

价值公式：

```text
价值 = 激活数 * 激活权重 + 付费数 * 付费权重
```

抖音只有一级内容类型且一级类型为“长视频”或“说唱”时，二级类型沿用一级类型。

第一版环比只基于本地上一周期数据；联网时间原因分析暂不实现。

## 目录口径

- `data/weeks/`：周度原始源文件。
- `data/months/`：月度原始源文件。
- `data/quarters/`：直接上传的季度原始源文件。
- `data/years/`：直接上传的年度原始源文件。
- `processed/`：清洗产物和核验 workbook。
- `.runtime/workflow.sqlite3`：权威运行库。
- `.runtime/top-assets/{platform}/{真实作品ID}/`：本项目复用或补采的素材库，同一真实平台作品跨周期只存一份；巨量素材 ID 不作为复用目录名。
- `config/channel_profiles.yml`：渠道识别配置；字段别名优先于通用字段映射。
- `config/field_mapping.yml`：通用字段别名。

## harvester 与环境变量

素材复制优先使用同级 `../harvester-THS` 的每日采集缓存；缺素材时才调用 harvester 的 TopN 补采。

如果本项目缺少飞书或 MiniMax 配置，会从 harvester 的 `.env` 补齐缺失项，不覆盖本项目已有值。

常用覆盖项：

```env
HARVESTER_ROOT=/Users/tjk/Documents/Codex/harvester-THS
MINIMAX_API_KEY=...
MINIMAX_MODEL=...
```

## 常用命令

短测试示例：

```bash
uv run pytest tests/test_streamlit_compat.py tests/test_cleaning_pipeline.py tests/test_multimodal_recap.py tests/test_recap_settings.py -q
```

命令行清洗示例：

```bash
uv run python main.py \
  --input data/weeks/20260518-20260524 \
  --period-start 2026-05-18 \
  --period-end 2026-05-24 \
  --processed-root processed \
  --db .runtime/workflow.sqlite3 \
  --ui-only
```

## 维护说明

- 渠道新增或关键词调整：优先改 `config/channel_profiles.yml`。
- 通用字段别名调整：改 `config/field_mapping.yml`。
- 飞书台账更新后，在 `本地总表` 重新读取线上飞书并更新本地总表；如果抖音、小红书或 B站最新投稿时间超过 3 天未更新，需要先人工确认再继续同步。
- 清洗和复盘可以分开重跑：清洗成功不依赖多模态复盘，复盘失败不破坏已清洗数据。
- 抖音历史台账维护命令：`uv run python scripts/douyin_history_ledger.py --help`。

详细同事操作说明见 [docs/同事使用说明.md](docs/同事使用说明.md)。
