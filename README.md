# River Observation Lake

河流观测研究站的离线观测湖表。接入员接纳每日读数，数据工程师发布固定批次集合，研究助理按站点和时间窗口读取、汇总并交付记录。仅依赖 Python 3.11 标准库和 SQLite，无网络服务。

## 运行

在项目根目录使用 Python 3.11；无需安装依赖：

```sh
python3.11 -B -m unittest discover -s tests -v
python3.11 -B -m obslake.product_service
```

运行入口见 .pointwise-runtime.json；run-manifest.json 列出完整套件及按场景选择的回归命令。测试和演示各自创建并清理独立临时目录。任何断言或操作异常使命令非零退出。演示显式读取 samples.json，实际接纳、发布、汇总、解码导出并重开核对，最后输出 POINTWISE_SCENARIO JSON。

## 一天的业务

1. 各站提交 batch_id 及离线记录；接入员完整验证四个字段，非法输入整批拒绝。
2. 接纳服务按 station、time、record_id 规范化并计算有明确字段边界的内容摘要，用于识别重交和冒名替换。
3. 普通段写入器编码完整记录、计算逐站时间界限，向内容寻址对象端口写入；只有明确成功才登记可发布批次。
4. 数据工程师提交有序批次集合；发布方校验对象，在一个事务中保存固定成员及描述并推进当前指针。
5. 研究助理按确切 snapshot_id、站点及 [start,end) 请求记录；扫描器安全裁剪并完整验证必要对象，成功后一次返回完整有序列表。
6. 汇总器逐站计算整数 count、sum、first_time、last_time，完整成果与确切来源请求一起提交。
7. 导出器保存固定字段次序的 UTF-8 JSON Lines 字节，供下游逐行核账；空扫描得到零字节。
8. 隔日重开，原成果直接从保存内容读取；新快照及源对象损坏不改变已经保存的交付。

## API

```python
import json
from pathlib import Path
from obslake import Lake

sample = json.loads(Path("samples.json").read_text(encoding="utf-8"))
with Lake("work") as lake:
    for batch_id, rows in sample["batches"].items():
        binding = lake.accept_batch(batch_id, rows)
    snapshot_id = lake.publish_snapshot(list(sample["batches"]))
    records = lake.read(snapshot_id, None, 10, 20)
    summary = lake.summarize(snapshot_id, None, 10, 20)
    exported = lake.export_product(snapshot_id, None, 10, 20)
    restored = lake.get_product(summary.product_id)
    assert restored == summary
    assert len(exported.export_bytes.splitlines()) == len(records)
```

也可独立使用 Catalog、IntakeService、SnapshotReader 和 obslake.product_service.ProductService。Lake 是同名接口的便捷组合；Catalog.current_snapshot_id() 返回当前 ID 或 None。

accept_batch 返回不可变 BatchBinding，字段为 batch_id、digest、segment。输入记录可为 Record、四项 tuple/list 或恰好含 record_id、station、time、value 的映射。身份须为非空且可用 UTF-8 编码的字符串；保留空白、大小写及原 Unicode 字符，不规范化字符。time、value 为非布尔整数，单位沿用上游采样刻度和原始计数，不转换为浮点数。批内 record_id 唯一；不同批次允许相同观测，扫描保留重数。验证完整消费输入后才开始写入。上游迭代异常原样传播。同 ID 同内容返回原固定绑定，异内容抛 ValueError。

publish_snapshot 返回固定字符串 ID。成员必须存在且无重复，顺序保留。空批次、空快照均合法。快照复制确切段描述；历史读取不重新选择批次或当前指针。

publish_coded(batch_id) 把单个已接纳批次发布为含固定 snapshot_id 的编码快照并推进当前指针，返回只含该批次的 ID；非法或未知 ID 抛 ValueError。发布先完整验证绑定普通段，再把其确切字节编成五片三取三码（总载荷不超过 5·⌈L/3⌉，每片等长且不超过 ⌈L/3⌉）。只有至少三片以期望身份确认并读回核验成功才提交，否则抛 Unavailable 并保持原快照、成员及批次绑定；重复调用可产生不同快照。编码描述含 kind=coded、original（原普通描述完整映射，其 object_id 仅用于核对、不是回退读取位置）、layout（版本、原长、原段摘要及分片解释）和按 index 排序的五项 shards（index、object_id、sha256、byte_length、payload_length）。元数据与分片头只含解释与完整性信息，不夹带观测记录或原文。

读取编码快照时每次都重新排除缺失、OSError、截短、同长损坏或同段同位替换的分片；至少三片有效即恢复并核对原长、原段摘要、普通格式、记录数与统计，不足三片抛 Unavailable，绝不回退读取原普通对象（原对象可保留供普通快照使用）。编码读取不会用原批次内容重新编码来绕过缺失分片。


read 返回 Record 列表，按 station、time、record_id、快照成员序号排序。station 为 None 或非空字符串，start/end 为非布尔整数且 start <= end。包含 start、排除 end。统计仅安全排除确定不相交段；被裁剪对象无需读取。任何必要对象必须先完整校验长度、SHA-256，再解码并核对格式、记录数及统计，即使请求只命中其中一条。未知快照、非法请求抛 ValueError；必要对象缺失、损坏或解码失败抛 Unavailable，绝不返回前缀列表。

summarize 和 export_product 返回不可变 SavedProduct，包含 product_id、request、summary、export_bytes。request 固定 kind、snapshot_id、station、start、end。summary 为按站点排序的 SummaryRow 元组，导出成果的 summary 为空；汇总成果的 export_bytes 为空。整数和任意精度计算，非空零和组保留，空结果无分组。为兑现无十进制位数上限的整数契约，包初始化在支持该功能的 Python 上将进程的 int 字符串转换位数上限设为 0。

导出逐行固定字段次序 record_id、station、time、value，json 使用 ensure_ascii=False、紧凑分隔符、UTF-8，每条末尾一个 LF；字符串内部换行转义。成果按格式版本、完整请求和内容确定身份，同一成果重复保存返回原身份。get_product 只读取保存内容，未知 ID 抛 ValueError，不扫描来源、不重算。

## 持久化及错误

工作目录包含 catalog.sqlite3 和 objects/。数据库记录批次绑定、快照、当前指针及成果 BLOB；普通段是单个内容寻址不可变对象，编码段的五个分片也各是一个内容寻址不可变对象。业务身份不拼入文件路径。相同对象字节可复用，已有对象损坏不得覆盖。ObjectWriteResult 明确报告成功或失败；失败不登记批次，真实 I/O 异常不吞掉。

## 编码表示不变量

- 五片始终按 0～4 独立寻址，载荷相同的分片仍是不同物理对象；分片头绑定片序、编码版本、原长、原段摘要与布局摘要，仅凭片序或文件存在不能取得输入资格。
- 编码布局记录原长、原段 SHA-256 及自己的摘要；恢复时按原长截断填充，填充不进入任何记录，恢复字节须逐字节等于原普通段。
- 快照按描述固定五片身份与解释，不把首次发布成功数当永久读取资格；每次读取重新核验物理片身份、长度和摘要，失败资格不缓存，来源恢复后可重读。
- 发布与读取双侧共用同一 codec：发布写分片并核对写回，读取排除不可用分片后交给解码器；两侧都以原段描述作为最终验收，任一侧都不能跳过完整校验。

快照成员和当前指针同事务提交；成果在全部源读取、计算、序列化完成后一次提交。失败无部分新快照或成果，已有成果保持。提交钩子支持确定性边界故障，测试使用真实 SQLite 和真实文件；不要求杀进程恢复或并发发布。未引用对象可留存，本版不回收。

重复打开只创建当前结构中缺失的表，不清空数据、重置指针、灌入样例或修复损坏对象。不支持的 schema 版本明确失败。样例由调用者显式接纳；修改样例后用旧批次 ID 重交会冲突，使用新 ID 才接纳新内容。直接修改内部数据库或对象不是业务更正接口。

## 手算与回归

samples.json 的 [B1,B2] 在 [10,20) 内有五条观测。桥站两条合计 0，首末时间 10/19；芦苇站三条合计 9007199254740993，首末 10/15。两条 a1 均保留，a2@20 排除。演示将实际导出解码后与独立样例逐条核对，并在当前快照变化后重开验证旧成果。

编码回归见 tests/test_coded.py：字节入口任意三、四、五片精确还原与两片拒绝，五、三、二份确认对应的发布可读/可读/拒绝，全部十种三片组合逐字节等同普通导出，删损重开、错段同位替换排除、裁剪不读片及失败不产出部分成果。coded_examples.py 的 check 核对新增能力，verify 先跑完整原回归再核对，demo 展示真实降级交付。

五个测试文件覆盖接纳与摘要边界、写入失败及不可变对象冲突、三处快照回滚边界、固定历史、时间裁剪与不裁剪逐条参照、编解码、精确整数、导出转义、保存成果重开、必要对象删除/截断/同长篡改、摘要匹配的非法段、后续成员失败和成果提交失败。完整 discover 始终保留。

## 源码职责

- 接纳：intake、validation、normalization、batch_digest、record_model、batch_registry。
- 普通段：segment_writer、segment_codec、segment_stats、segment_descriptor、object_store。
- 编码段：segment_codec（五取三字节编解码与分片封装）、segment_descriptor（coded 描述及写片）、coded_publish。
- 固定目录：catalog、catalog_schema、snapshot_commit。
- 读取：snapshot_reader、segment_reader、range_scan、integrity、scan_request。
- 成果：aggregation、export、product_model、product_store、product_service。

frozen-project.json 记录当前公开格式与排序口径。全部 25 个业务模块（原 24 个加编码发布）均由上述实际流程调用。
