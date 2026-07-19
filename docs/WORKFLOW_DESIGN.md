# Agent 工作流设计与后续路线

## 当前 V2 原则

1. **数字确定性优先**：价格、利率、宏观序列由代码获取；agent 不负责抄数字。
2. **研究与核验分离**：Scout 找证据，Verifier 检查摘录语义，Source Auditor 重新抓 URL。
3. **观点必须可反驳**：Rank 后由 Risk Challenger 找反证，Write 必须给出失效条件。
4. **运行模式显式化**：盘前、盘中、收盘不能共享相同的 bar 语义或输出文件。
5. **成本分层**：standard 与 deep 的 agent 数、审稿模型和预算不同。

## 不建议继续“无差别加 agent”

更多搜索 agent 会带来三个典型问题：同一 Reuters 新闻被重复发现、网页上下文 token 急剧增加、多个 agent 形成相同共识。新增 agent 应满足至少一项：覆盖新的资产类别、访问新的独立一手来源、承担反证角色、或减少人工审计工作。

## 已实现的 V3 能力

### 历史记忆与变化检测

本地 `state/market_brief.sqlite3` 已记录每日确定性快照、核心 thesis、source-confirmed claim、agent 指标和未来催化剂。

在调用 agent 前由代码计算昨日、5 个同模式观测和 20 个同模式观测的变化；历史不足时明确返回 unavailable，不制造趋势。

- 今日相对昨天/5 日/20 日真正变化了什么；
- 市场宽度、因子、信用与波动率是否连续恶化或反转；
- 昨日 thesis 是否仍成立；
- 前几天列出的 catalyst 是否兑现、落空或延期。

Rank/Write 直接读取这个有界上下文，只解释优先级最高的变化，不重新转录整个数据库。

### 事件驱动的动态路由

`calendar-router` 以低成本读取官方经济、央行和公司 IR 日历：

- CPI/FOMC 日增加 inflation、rates、Fed 三个深度 agent；
- 大型科技财报日增加 earnings/AI supply-chain agent；
- 油价或汇率异常时增加 energy/geopolitics 或 FX/central-bank agent；
- 安静交易日减少搜索，把预算留给 Rank/Challenge。

当前 quiet/normal/deep 基础角度为 6/9/12，并按事件追加专门角度，最终限制为 standard 13、deep 16。Router 证据仍需经过 Source audit 才能进入正文。

### 结果评分与事后复盘

Rank 强制输出机器可读的 `horizon_days`, `confirm_condition`, `invalidate_condition`, `sources`, `source_angles`。SQLite 按 NYSE 交易日生成 1 日和 5 日到期日，再由只读 `postmortem-analyst` 判断：

- 原因判断正确但价格未响应；
- 数据/新闻事实错误；
- 催化剂方向正确但时间错误；
- 纯叙事、没有可检验内容。

`out/metrics.json` 持续统计 agent 的证据通过率、进入 Rank 的数量，以及催化剂兑现对 agent/来源的反向归因。

## 下一阶段优先级

### P1：更可靠的数据与真实资金流

当前已加入 Tradier/Yahoo 期权链、CFTC COT 和 FINRA Reg SHO，但后两者只是持仓/活动代理。Yahoo/FRED 适合低成本原型；若报告用于重要资金决策，仍应接入：

- 带 SLA 的实时/延迟行情 API；
- 官方 BLS/BEA/Census/Fed 发布日历；
- 公司 IR/SEC filing feed；
- Treasury auction、央行会议和主要财报日历；
- 信用利差、期权偏度、期货持仓与资金流的可靠数据源。

真正可交易的日频 ETF/共同基金净流、dealer positioning、逐笔期权方向和完整 OPRA 数据通常需要有许可与 SLA 的商业数据。不可用“短成交量”“期货净持仓变化”或“ETF 成交额”冒充资金净流。

每个来源需要 `retrieved_at`, `source_timestamp`, `freshness_sla`, `license` 和 fallback 规则。

### P2：区域与语言专门化

全球报告不应完全依赖英文媒体转载。deep 模式可增加：

- 中国：央行、统计局、财政部、交易所与公司公告；
- 日本：BoJ、财务省、交易所与公司 IR；
- 欧洲：ECB、Eurostat、各国统计机构；
- 商品：EIA、OPEC、IEA、CFTC。

区域 agent 先输出英文/本地语言证据与标准化 claim，最终 Writer 才统一翻译成中文。

### P2：组合相关性（可选）

若用户提供只含 ticker/权重的本地观察列表，可增加 `portfolio-impact` agent：把宏观与主题映射到组合暴露、相关性和事件风险。它不应生成交易指令，只输出“影响路径、关键监控值、失效条件”。持仓文件必须保持本地并排除 Git。

## 推荐最终形态

```text
Deterministic data + history delta + event router
                 │
       relevant specialist scouts
                 │
  excerpt verifier → source auditor
                 │
       cross-asset rank strategist
                 │
       independent risk challenger
                 │
      writer → deterministic report gate
                 │
       1d/5d postmortem scoreboard
```

最值得下一步实现的是“历史变化检测 + 事后评分”，而不是再增加固定搜索角度。它能让报告从每日新闻汇总升级为可积累、可校准的研究系统。
