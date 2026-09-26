"""支出异动解释后台门面：串联口径、资料、归因、版本与报告。

用法概览：

1. ``register_scope`` 冻结比较口径（人群、药品目录、地区、窗口）；
2. ``ingest`` / ``mark_reported`` 接入六类资料并登记报送情况；
3. ``publish`` 生成一版冻结结果（数据修订、回补、目录变化、
   漏报补报都只产生新版本）；
4. ``submit_attribution`` / ``set_attribution_status`` 维护竞争性归因；
5. ``report`` 输出决策者视图，``impact`` 说明两次版本间的影响，
   ``public_export`` 生成符合隐私阈值的公开材料。
"""

from __future__ import annotations

from src.aggregation import WindowAggregate, aggregate_window
from src.attribution import Attribution, AttributionRegistry, AttributionStatus, Evidence, Hypothesis
from src.ingestion import DataStore, IngestBatch
from src.models import FrozenScope, Source
from src.privacy import PrivacyPolicy, PublicExport, build_public_export
from src.reporting import DecisionReport, build_report, format_impact
from src.signals import SignalConfig
from src.versioning import ResultVersion, RevisionCause, VersionDiff, VersionStore


class ExplanationBackend:
    """支出异动解释后台。"""

    def __init__(
        self,
        privacy_policy: PrivacyPolicy | None = None,
        signal_config: SignalConfig | None = None,
    ) -> None:
        self.store = DataStore()
        self.attributions = AttributionRegistry()
        self.versions = VersionStore()
        self.privacy_policy = privacy_policy or PrivacyPolicy()
        self.signal_config = signal_config or SignalConfig()
        self._scopes: dict[str, FrozenScope] = {}

    # ---- 口径 ----

    def register_scope(self, scope: FrozenScope) -> str:
        """登记冻结口径，返回 ``scope_id``（同内容重复登记幂等）。"""
        self._scopes[scope.scope_id] = scope
        return scope.scope_id

    def scope(self, scope_id: str) -> FrozenScope:
        """按标识读取冻结口径。"""
        return self._scopes[scope_id]

    # ---- 资料接入 ----

    def ingest(self, source: Source, records: list, note: str = "") -> IngestBatch:
        """摄取一批资料记录。"""
        return self.store.ingest(source, records, note=note)

    def mark_reported(self, source: Source, month: str, region: str) -> None:
        """登记某地区某月已报送（区分真零值与未报送）。"""
        self.store.mark_reported(source, month, region)

    # ---- 版本 ----

    def publish(
        self,
        scope_id: str,
        cause: RevisionCause,
        note: str = "",
    ) -> ResultVersion:
        """按当前资料与指定口径发布一版冻结结果。"""
        scope = self.scope(scope_id)
        aggregate = aggregate_window(scope, self.store)
        return self.versions.publish(cause, scope, aggregate, note=note)

    # ---- 归因 ----

    def submit_attribution(
        self,
        scope_id: str,
        author: str,
        hypothesis: Hypothesis,
        effect_low_yuan: int,
        effect_high_yuan: int,
        rationale: str,
        evidence: list[Evidence],
        competition_group: str | None = None,
    ) -> Attribution:
        """提交一条归因主张；口径必须先登记。"""
        if scope_id not in self._scopes:
            raise KeyError(f"未登记的口径：{scope_id}")
        return self.attributions.submit(
            scope_id=scope_id,
            author=author,
            hypothesis=hypothesis,
            effect_low_yuan=effect_low_yuan,
            effect_high_yuan=effect_high_yuan,
            rationale=rationale,
            evidence=evidence,
            competition_group=competition_group,
        )

    def set_attribution_status(
        self, attribution_id: str, status: AttributionStatus, note: str = ""
    ) -> Attribution:
        """流转归因状态。"""
        return self.attributions.set_status(attribution_id, status, note)

    # ---- 输出 ----

    def report(self, version_id: str | None = None) -> DecisionReport:
        """生成某版（缺省最新版）结果的决策者视图。"""
        version = self._resolve(version_id)
        return build_report(version, self.attributions, self.store, self.signal_config)

    def impact(self, old_id: str, new_id: str) -> tuple[VersionDiff, str]:
        """比较两版结果，返回差异及其文字说明。"""
        diff = self.versions.diff(old_id, new_id)
        return diff, format_impact(diff)

    def public_export(self, version_id: str | None = None) -> PublicExport:
        """生成某版（缺省最新版）观察窗口的公开导出（已做隐私抑制）。"""
        version = self._resolve(version_id)
        scope = version.scope
        observation = set(scope.observation_months)
        records = [
            record
            for record in self.store.latest(Source.SETTLEMENT)
            if record.month in observation
        ]
        return build_public_export(records, scope.regions, scope.drug_ids, self.privacy_policy)

    def _resolve(self, version_id: str | None) -> ResultVersion:
        if version_id is not None:
            return self.versions.get(version_id)
        latest = self.versions.latest()
        if latest is None:
            raise ValueError("尚未发布任何版本")
        return latest
