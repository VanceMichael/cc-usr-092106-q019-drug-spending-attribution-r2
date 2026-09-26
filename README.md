# 重点药品支出异动解释

保存重点药品支出变化、比较口径和归因证据，并提供一套支出异动解释后台：冻结比较口径、接入多源资料、登记竞争性归因、版本化汇总结果、输出决策报告与隐私合规的公开材料。

## 为什么需要解释后台

重点监测药品支出明显下降后，不能把全部减少额直接写成反欺诈成效——价格调整、供应短缺、患者结构变化、治疗替代和结算延迟可能同时发生，过度压降还可能意味着真实患者取药受阻。后台的职责是让每一版汇总判断都可解释、可回溯、可比较。

## 能力总览

| 模块 | 职责 |
| --- | --- |
| `src/models.py` | 冻结比较口径（人群、药品目录、地区、基线与观察窗口），内容哈希即口径标识；六类资料记录模型 |
| `src/ingestion.py` | 只增不改的资料登记处；修订以新记录体现；报送登记区分"真零值"与"未报送" |
| `src/aggregation.py` | 按冻结口径计算支出异动；未覆盖地区显式列出、绝不按零计入 |
| `src/attribution.py` | 竞争性归因登记：证据、不确定性区间、竞争组互斥加总、过度认领警示 |
| `src/versioning.py` | 结果版本链：数据修订、跨月回补、目录变化、漏报补报只生成新版；版本差异精确分解为覆盖/目录/修订效应 |
| `src/privacy.py` | 公开导出隐私规则：最小患者数阈值、互补抑制、分组合计限制、总额取整 |
| `src/signals.py` | 就医中断与数据质量信号：取药受阻、供应受限、结算延迟 |
| `src/reporting.py` | 决策者视图：下降在哪里、哪些解释被支持、是否伴随中断信号、口径变化影响 |
| `src/backend.py` | `ExplanationBackend` 门面，串联以上全部能力 |

## 关键规则

- **口径冻结**：比较人群、药品口径、地区范围、观察窗口一旦登记不可修改；任何变化生成新口径（新 `scope_id`）。
- **缺失不是零**：地区漏报时整体排除出合计并显式列出；已报送但无记录才是真零值。
- **只增不改**：数据修订、跨月回补、目录变化、漏报补报都只产生新版本，历史版本可回溯、可比较。
- **归因竞争**：分析师可提交彼此竞争的归因并附证据与不确定性区间；同一竞争组内取并集跨度而非加总，防止重复计入成效。
- **隐私阈值**：单元格窗口内每月患者数均达阈值才可公开；组内仅剩一个被抑制格时追加互补抑制；含抑制格的分组不发布精确合计；全国总额取整发布，公开材料不得反推个人。

## 快速开始

```python
from pathlib import Path
from src.backend import ExplanationBackend
from src.attribution import Evidence, Hypothesis
from src.sample_data import ingest_initial, load_sample_panel, make_scope
from src.versioning import RevisionCause

panel = load_sample_panel(Path("fixtures/sample_panel.json"))
backend = ExplanationBackend()
scope_id = backend.register_scope(make_scope(panel))
ingest_initial(backend, panel)          # 接入结算、价格、供应、诊疗量、核查、反馈

v1 = backend.publish(scope_id, RevisionCause.INITIAL)
report = backend.report(v1.version_id)  # 下降在哪里、哪些解释被支持、中断信号、注意事项

backend.submit_attribution(
    scope_id, "分析师甲", Hypothesis.ANTI_FRAUD, 4_000_000, 5_500_000,
    "核查行动与下降同步", [Evidence("aggregate", v1.version_id)],
)
```

示例面板（`fixtures/sample_panel.json`，虚构数据）演示了完整剧情：R4 漏报后补报（覆盖效应）、目录调出药品（目录效应）、R3 跨月回补（结算延迟信号）、R2 量降价稳反馈升（疑似取药受阻）、D03 价降量稳（价格调整）、D05 满足率走低（供应短缺）。

## 领域资料

仓库中的 `contracts/context.schema.json` 描述基础资料格式，`fixtures/context.json` 给出可公开使用的示例；`contracts/scope.schema.json` 与 `contracts/attribution.schema.json` 分别描述冻结口径与归因主张的格式。代码库只负责读取与校验这些资料，业务服务可沿用相同标识和版本约定。

当前资料反映的事实包括：

- 重点监测药品支出减少约四百五十亿元
- 支出变化可能受多种因素影响
- 监管成效不能替代患者用药可及性判断

## 本地校验

运行项目自带测试即可确认样例资料可读取、领域标识与版本字段完整，以及后台全部规则按预期工作：

```bash
python3 -m pytest tests/
```

所有示例均为虚构数据，不含真实个人信息、账号或访问凭据。
