# semiconductor_reversal Prompt

用中文评估半导体链路里的业绩爆发、估值反转、长期 call 风险收益。

策略先使用当前真实仓位、期权底层、paper positions 和 SNXX/SNDK 合并暴露建立 portfolio context，再执行 strategy_news_scout 补齐证据，最后挖掘和组合候选因子：
- catalyst_keywords: 订单、认证、指引、收入增长、利润率改善等新闻催化。
- risk_keywords: 增发、下调指引、延迟、持续经营、监管等风险。
- chain_relevance: 半导体产业链位置和 AI/硅光/先进封装/设备等链路。
- intraday_followthrough: 本地 quote 行为系统计算的日内涨跌、缺口、冲击和延续性，不由 LLM 判断。
- large_move_reversal_risk: 大涨/大跌后的回撤、获利回吐和追高惩罚。
- position_crowding_pressure: 当前真实/纸面持仓较大且日内波动大时降低新 paper order 意愿。
- volume_confirmation: 本地成交量字段确认；未来升级为相对历史量。
- underlying_relation_strength: 通过产品说明、ETF/prospectus/issuer 页面、新闻共现推断经济底层关系，例如 LITX 需要通过证据连接到 LITE，而不是硬编码。
- earnings_surprise: 财报收入、EPS、毛利率或现金流超预期，官方 IR/公告/10-Q 优先。
- guidance_revision: 下季或全年指引上修、订单可见度改善、管理层语气变化。
- datacenter_mix_shift: 数据中心、AI 推理、企业 SSD 或高端存储占比提升。
- contracted_revenue_visibility: NBM、长期供货、设计导入、客户认证和合同化收入线索。
- product_roadmap_acceleration: HBF、BiCS8、QLC、封装路线提前或量产节点提前。
- hbf_ai_inference_moat: HBF 对 AI 推理存储瓶颈、成本/带宽/功耗的结构性优势。
- official_source_strength: 官方公告、IR PDF、SEC、earnings transcript 的证据强度。
- sell_the_news_volatility: 财报后涨幅过大、短线获利回吐、期权隐波或成交拥挤风险。
- serenity_chain_readthrough: 关注 Serenity (@aleabitoreddit) 风格的半导体链路、周期、库存、订单和 follow-through 线索。
- leopold_compute_demand: 关注 Leopold (@leopoldasch) 风格的 AI compute、GPU/ASIC、frontier lab capex、数据中心和电力约束线索。
- ai_compute_macro_risk: 出口限制、电力/数据中心瓶颈、capex pause/digestion 等宏观风险。
- price_momentum: 受限的日内动量/反转输入。
- high_impact_news: 重大新闻数量，避免重复标题堆叠。
- portfolio_context: 当前持仓或底层暴露。

SNXX 必须按 SNDK 的 2x 做多底层暴露解释，事实回顾以 Sandisk/SNDK 为主。LITX 等产品型标的必须先尝试发现底层/相关公司关系，并在报告中标明证据和置信度。不得直接写“未发现公开新闻”或“待查证”；必须先列出已执行的 strategy search query、命中来源和失败来源。若出现 missing_evidence、research_gap、因子样本不足或 gate 失败，先触发二级搜索，再评分。

输出研究候选、factor_breakdown、日常行为数值、底层关系证据、策略自主深挖证据、因子权重变化、risk gate 结论和 paper order 原因或阻断原因。真实交易禁止；通过回测、drawdown、证据和风险门后才允许进入 paper_buy/paper_sell 队列。API 只负责解释本地数值系统的综合结果，不负责替代本地因子计算。
