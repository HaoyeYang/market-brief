# 数据来源、限制与升级路径

## 当前自动接入

| 数据 | 当前来源 | 频率/时效 | 能回答什么 | 不能回答什么 |
|---|---|---|---|---|
| 现货、ETF、期货、FX、商品 | Yahoo Finance / yfinance | best-effort，无 SLA | 日线水平、变化、跨资产状态 | 机构级实时性、交易所完整性 |
| 美国利率与宏观 | FRED CSV | 随源更新，可能晚于当日发布 | 已发布历史序列 | 市场一致预期、即时电讯稿 |
| ETF 期权 | Tradier（有令牌）或 Yahoo fallback | 成交量当日累计；OI 通常为上一清算周期 | put/call、IV、偏度、跨式隐含波幅、OI 集中 | 主动买卖方向、dealer inventory、可靠 GEX |
| 期货持仓 | CFTC COT Public Reporting Environment | 周二头寸，通常周五发布 | 资产管理、杠杆基金、managed money 的净持仓与周变化 | 实时流入流出、场内日内仓位 |
| 短成交活动 | FINRA consolidated Reg SHO daily file | 上一交易日 | FINRA 报告场所的 short-volume ratio | 全市场 short interest、净做空、基金资金流 |

所有记录保留 `source`、数据日期/到期日和限制说明。某个可选来源失败不会中止整份报告，但报告必须将其标为 unavailable。

## “开源资金流”能做到的边界

免费官方数据可以提供有价值但较慢的代理：CFTC 周度持仓变化、FINRA 日度短成交活动、SEC 基金披露和 Treasury TIC 跨境资金（后两者频率和发布时间都较低）。这些适合观察拥挤度和中期配置变化，不适合声称“今天资金净流入某资产”。

日频 ETF creations/redemptions、共同基金净申赎、跨资产机构 flow、期权逐笔方向和 dealer gamma 通常来自发行人逐日文件或 EPFR/LSEG/Bloomberg/Cboe LiveVol 等许可数据。若没有同口径的 shares outstanding/NAV、修订规则和 SLA，就不应自行拼成精确净流。

## 推荐升级顺序

1. **期权生产数据**：配置 `TRADIER_TOKEN`，验证账户对应的实时/延迟权限；资金决策级用途再评估 Cboe DataShop/LiveVol 或完整 OPRA 供应商。
2. **行情 SLA**：用持牌实时/延迟行情 API 替换 Yahoo；适配器输出仍保持现有 JSON 结构，方便双源对账。
3. **真实 fund flow**：选择覆盖所需地区/基金类别的商业供应商，要求净申赎定义、修订历史、时间戳、许可和 SLA；先双跑 20 个交易日再切主源。
4. **信用**：增加指数 OAS/CDX 等可靠源；不要用 HYG/LQD 价格完全替代信用利差。

## 接入新供应商的验收条件

- `retrieved_at`、供应商的 `source_timestamp`、交易日和时区明确；
- 每字段说明 real-time/delayed/EOD、许可范围和 freshness SLA；
- 主源/备源同口径对账并设置容差，超差时拒绝发布而不是静默选择；
- 认证令牌仅在本机安全环境中，永不进入 Git、日志或报告；
- 历史修订可追溯，SQLite 保留来源与版本；
- 新的 flow 字段必须区分净流、成交额、持仓变化和短成交活动。

## 官方参考

- [Tradier Options Chains API](https://docs.tradier.com/reference/brokerage-api-markets-get-options-chains)
- [CFTC Commitments of Traders 说明](https://www.cftc.gov/MarketReports/CommitmentsofTraders/AbouttheCOTReports/index.htm)
- [FINRA Daily Short Sale Volume Files](https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files)
- [OCC Daily Volume and Open Interest](https://www.theocc.com/market-data/market-data-reports/volume-and-open-interest/daily-volume)
- [Cboe Historical Put/Call Ratios](https://www.cboe.com/us/options/market_statistics/historical_data/)
- [Polygon/Massive Options API](https://polygon.io/docs/options/getting-started)
