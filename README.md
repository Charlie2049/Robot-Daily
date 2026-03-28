# Robot Daily

> 全球机器人 / Physical AI 最新资讯的开源存档。

## 目标
- 快速聚合全球机器人产业的关键事件（产品发布、政策、资金、趋势分析）。
- 用结构化 JSON 保存原始数据，便于搜索、二次开发或接入 API。
- 自动渲染 Markdown / 静态页面，供 GitHub Pages 或其他前端展示。

## 项目结构
```
Robot-Daily/
├── data/                 # 原始数据集，按 YYYY/MM/dd.json 划分
├── content/              # 渲染后的 Markdown 摘要，可直接发布
├── scripts/              # 工具脚本（例如渲染 Markdown）
└── README.md
```

### 数据格式
`data/YYYY/MM/DD.json` 为数组，每条对象包含：
```jsonc
{
  "id": "2026-03-25-zhongguancun-humanoids",
  "date": "2026-03-25",
  "title": "事件标题",
  "category": ["humanoid", "trend"],
  "region": "China",
  "summary": "核心事实",
  "source": "Reuters",
  "source_url": "https://…",
  "tags": ["robotics", "physical-ai"],
  "impact": "对行业/企业意味着什么"
}
```

### 渲染脚本
`scripts/render_markdown.py` 可把指定日期的数据转成 Markdown：
```bash
python scripts/render_markdown.py 2026-03-28
```
生成的 `content/2026-03-28.md` 可直接复制到博客或 Newsletter。

> 脚本也支持 `--json path/to/file.json --output custom.md` 自定义输入输出。

## 下一步想做的
1. **GitHub Actions**：每天定时抓取/生成最新 JSON & Markdown，并部署到 GitHub Pages。
2. **前端展示**：用 Astro / Next.js / VitePress 渲染时间线、分类筛选、RSS。
3. **多语言支持**：在 JSON 中增加 `summary_en`、`impact_en` 字段，实现中英双语。
4. **API**：通过 Cloudflare Workers / Vercel Edge 暴露读写接口，方便外部调用。

欢迎通过 Issue/PR 提交新的线索、数据源或者工具脚本 🙌
