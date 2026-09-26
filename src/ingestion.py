"""多源资料接入：只增不改的登记处。

设计约束：

- 资料只增不改。数据修订、跨月回补都以更高摄取序号的新记录进入，
  查询时同一业务主键取序号最高者，历史值仍可追溯。
- 报送登记与记录分离：某地区某月"已报送但无记录"是真零值，
  "未报送"是覆盖缺失，两者在聚合时必须区别对待
  （未覆盖区域不得当作零值）。
- 摄取序号单调递增，供结算延迟（跨月回补）分析判断
  某笔费用是否晚于更晚月份到达。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import Source, check_month, record_key, record_type


@dataclass(frozen=True)
class IngestBatch:
    """一次摄取批次。"""

    batch_id: str
    seq: int
    source: Source
    record_count: int
    note: str = ""


class DataStore:
    """六类资料的只增不改登记处。"""

    def __init__(self) -> None:
        self._seq = 0
        # source -> 业务主键 -> [(摄取序号, 记录), ...]
        self._records: dict[Source, dict[tuple, list[tuple[int, object]]]] = {
            source: {} for source in Source
        }
        # (source, month, region) 已报送登记
        self._reports: set[tuple[Source, str, str]] = set()
        self._batches: list[IngestBatch] = []

    def ingest(self, source: Source, records: list, note: str = "") -> IngestBatch:
        """摄取一批记录，返回批次凭证。

        记录类型必须与来源匹配；同主键记录保留全部历史，
        查询时以最高序号为准。
        """
        source = Source(source)
        expected = record_type(source)
        records = list(records)
        for record in records:
            if not isinstance(record, expected):
                raise TypeError(
                    f"{source.value} 来源只接受 {expected.__name__}，收到 {type(record).__name__}"
                )
        self._seq += 1
        batch = IngestBatch(
            batch_id=f"batch-{self._seq:06d}",
            seq=self._seq,
            source=source,
            record_count=len(records),
            note=note,
        )
        table = self._records[source]
        for record in records:
            key = record_key(source, record)
            table.setdefault(key, []).append((self._seq, record))
            region = getattr(record, "region", None)
            if region:
                self._reports.add((source, record.month, region))
        self._batches.append(batch)
        return batch

    def mark_reported(self, source: Source, month: str, region: str) -> None:
        """登记某地区某月已报送（即使没有任何记录）。

        用于区分"已报送的零值"与"未报送的覆盖缺失"。
        """
        source = Source(source)
        self._reports.add((source, check_month(month), region))

    def is_reported(self, source: Source, month: str, region: str) -> bool:
        """某地区某月是否已报送。"""
        return (Source(source), check_month(month), region) in self._reports

    def reported_regions(self, source: Source, month: str) -> set[str]:
        """某月已报送的地区集合。"""
        source = Source(source)
        check_month(month)
        return {r for s, m, r in self._reports if s is source and m == month}

    def latest(self, source: Source) -> list:
        """返回某来源全部记录的最新值（每主键取最高摄取序号）。"""
        source = Source(source)
        return [entries[-1][1] for entries in self._records[source].values() if entries]

    def latest_with_seq(self, source: Source) -> list[tuple[int, object]]:
        """返回某来源全部记录的最新值及其摄取序号。"""
        source = Source(source)
        return [entries[-1] for entries in self._records[source].values() if entries]

    def batches(self) -> list[IngestBatch]:
        """返回全部摄取批次（按序号升序）。"""
        return list(self._batches)
