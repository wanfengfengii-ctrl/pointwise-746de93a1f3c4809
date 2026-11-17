# 编码观测段：公开交付契约

现有 README.md、samples.json、全部原测试与 V1 API 保持有效。本题共同结果是：一个固定编码快照所需段的任意两片不可用后，仍交付原记录、精确汇总和相同导出字节；少于三片时整份新成果失败。新增的是编码发布与固定读取的交接，不是原件修复。

## 业务入口与持久表示

在 Lake 和 Catalog 上新增 publish_coded(batch_id)，参数是已接纳的批次 ID，返回只含该批次的固定 snapshot_id 并推进当前指针。非法或未知 ID 抛 ValueError。先完整验证绑定的普通段，再编码原段字节；不改 batches 中原绑定，不改历史快照。允许重复调用产生不同快照 ID。原 publish_snapshot 继续发布普通段。

扩展 descriptor_to_mapping / descriptor_from_mapping，使快照的描述能 JSON 往返并关闭重开。coded 描述有四个顶层字段：
- kind：字符串 coded。
- original：原 OrdinarySegmentDescriptor 的完整映射，用来核对恢复后的原段；其中 object_id 不是回退读取位置。
- layout：编码器返回的 JSON 对象，含编码版本、原始长度、原段 SHA-256 及解释分片所需的布局。其他编码参数自行设计并完整持久化。
- shards：按 index 排序的五项列表。每项固定含 index（0～4）、object_id、sha256、byte_length、payload_length。object_id 是传给原对象端口的身份，sha256 是完整物理分片对象的 SHA-256，byte_length 是其实际字节数，payload_length 是该片编码载荷字节数。

同一段的五个分片必须分别可寻址，载荷相同也不能合并为同一物理对象。物理封装自行设计，但须绑定原段、layout 和片序；仅凭一个片序或文件存在不能取得输入资格。编码元数据和分片头只保存解释及完整性信息，不得夹带观测记录、原文副本或其他恢复载荷。不得在数据库或新辅助对象中另存恢复原文。原普通对象可保留供普通快照使用，编码读取不得访问它或用原批次重编码绕过分片不足。

## 编码字节入口

提供 obslake.segment_codec.encode_coded_bytes(raw) 和 decode_coded_bytes(layout,pieces)，便于独立核对同一业务编码。raw 为任意 bytes（含空串）。encode_coded_bytes 返回 (layout,payloads)：layout 是可 JSON 往返的对象，payloads 是按 0～4 排列的五项 bytes 序列。设 L=len(raw)，每片载荷等长且不超过 ceil(L/3) 字节，总载荷不超过 5*ceil(L/3)。物理分片头不计载荷容量，但不能藏恢复数据。

pieces 是 {片序:完整正确载荷}，片序互异，来自同一次 encode_coded_bytes；decode_coded_bytes 必须在任意三、四、五片下精确返回 raw，少于三片抛 obslake.Unavailable。字节入口不要求独立校正未经验证的坏载荷；湖段读取必须先按清单核验物理片、身份、长度和摘要，才可把其载荷交给解码器。最终原长、原段摘要、普通格式、记录数及统计均须核对；填充不得进入原段。

示例 raw=b'ABCDEFG'，五片各至多三字节；只给 0/1/2、0/3/4、2/3/4 均须得到 b'ABCDEFG'。字节算法与物理布局自行选择，不规定有限域、生成矩阵或封装格式。

## 发布与读取失败边界

编码发布尝试五片。每片 ObjectWriteResult.success 为假、确认身份不符，或该片写入/读回产生 OSError、长度或摘要不符，均视为该片不可用，继续处理其他片。至少三份正确身份的成功确认及读回验证才可调用原快照提交；不足三片抛 Unavailable，保持原快照、快照成员及批次绑定。其他异常原样传播，不提交。已写入未引用对象允许留存，不覆盖已有损坏对象；无需清理、修复或杀进程恢复。

固定快照保留五片的身份及解释，不把首次发布成功数当成永久读取资格。每次必要段读取重新排除缺失、OSError、截短、同长损坏或错段替换的分片。至少三片有效即须恢复成功；不足三片抛 Unavailable，不返回前缀记录，不发布新的部分汇总或导出。来源恢复后可重新读取，不持久缓存失败资格。旧保存成果仍只读保存内容。

V1 的安全裁剪仍有效：空窗口、不相交站点或时间范围无需读取被裁剪段。必要段即使只命中一条记录，也要完整校验参与恢复的分片并核对恢复后的完整原段。排序、重数、大整数、精确汇总及导出格式沿用 V1。允许完整读所有片、完整恢复及全量重算；不要求局部解码、并发、分片修复或回收。

## 可运行样例与交付

coded_examples.py 是非答案夹具，v1 子命令只核对已有能力；check 核对新增能力，verify 先执行完整原回归再核对新增能力，demo 展示真实降级交付。初始 V1 没有新增入口，因此 check/verify/demo 的新增部分留待实现后运行，不把缺失能力当现有业务故障。

五、三、两份确认成功分别验证发布可读、发布可读及发布拒绝；已发布后的全部十种三片组合须逐字节还原，真实记录、汇总及导出也逐项一致。发布后删一片、同长损坏另一片并重开仍须成功；再损坏一片须整份失败且无新成果。换入其他段同片序的对象须排除。快照提交钩子失败后原指针及成员不变，重试可成功。

使用 Python 标准库和 SQLite，保留全部原测试字节。扩展发布职责与段描述解释、读取职责，提交简短的表示不变量和双侧交接说明。沿现有 .pointwise-runtime.json 接通完整业务核验和降级演示：保留 version=1、kind=cli、setup 的 argv 列表及 verify/demo 的 argv 数组形式；verify 调用 python3.11 -B coded_examples.py verify，demo 调用 python3.11 -B coded_examples.py demo。核验失败必须非零退出。run-manifest.json 的原回归仍可独立执行。
