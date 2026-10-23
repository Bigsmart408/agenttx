# TB 连接拒绝故障后的恢复（2026-10-23）

用户要求从连接拒绝位置开始补跑，重启后汇报实际执行与验收成功进度；禁止改动 8080 转发。

## 已验证的修复

普通 bridge 出网被宿主机现有宽泛 PREROUTING 重定向影响。保留这些规则，任务改用独立网络命名空间及 slirp4netns 通用用户态出网。该方式不监听宿主机端口，不部署模型接口代理，不改变模型地址或配置，也不写任何宿主机防火墙规则。可信网络辅助容器只为对应私有网络打开常规出站连接，不执行任务代码；任务容器不再使用 host 网络。

真实容器验证：宿主机与任务网络命名空间不同；在任务网络创建/删除空 NAT 链后，宿主机 NAT 规则（忽略计数器与生成时间）不变；隔离环境鉴权 `/v1/models` 返回 200，DNS 可解析国内镜像域名。验证不消耗模型 token。证据：`validation/tb-api-connection-repair-20261023/isolation-verification.json`。

出网 helper 固定为 `sha256:10b423d23527e36da72c554fa82426553a0a8474926e5dcc80048dda358baf0c`。网络工具取自国内 Ubuntu 镜像及本机 Ubuntu 24.04 运行库，构建输入、哈希与日志在 `validation/tb-isolated-egress-helper-20261023/`；不改实验任务镜像与测试。

## 队列与计量

活动队列：`tb-dual-native-full-20261023-r18-network-resume`；本机服务：`agenttx-tb-dual-full-20261023-r18-network-resume.service`。

继承 r17 的首轮和重跑记录，不覆盖原文件；接管尚在运行的 3 个旧 worker，自然结束后回收旧调度器，其间总并行数最多 8。已完成但未被旧暂停调度器登记的 5 个 worker 已按原结果登记。

初始识别 132 个需要优先修复的单元：44 个具有模型连接错误摘要，88 个是已确认故障时窗内用量不完整的推定受影响单元。时窗从 UTC 2026-10-22 20:56 起（北京时间 10-23 04:56）；推定与直接证据分别记录，不把推定当成逐条确证。

现有未开始的失败重跑复用原计划；此前重跑也遭遇连接故障的单元另记一次环境修复尝试。按源失败时间排序，先补 create-bucket、hf-lora-adapter 等最早受影响单元，然后继续待跑首轮和既有失败重跑。每个单元本次环境修复最多一次，不按最好结果挑选。

所有旧尝试＋普通重跑＋本次修复尝试的 main/replay token 累加；任一尝试账单不完整，该单元排除完整成本配对，已知总量仍仅是下界。首轮 effects 与累计重跑报告分开，网络配置变化已登记，不将新出网方式默认为旧环境完全等价。

每 30 秒鉴权检查原 API，失败时暂停新派发；新 worker 出现未识别环境异常则停止继续派发并保留诊断。已识别模型预算或模型时限退出沿用原有例外分类。监控识别 stopped/zombie 调度器；Clean 保留完整轨迹，其余按既有摘要保留政策处理。

模型、reasoning、上下文表、种子、原测试、时限及禁用磁盘门槛均保持原配置。启动不等于验收成功，后续报告分别列实际模型用量、已完成与通过数。

## 代码和核验

本机 worker 源码只替换 `bench_tb_image_native.py` 的默认网络与 `tb_task_services.py` 的任务网络实现，其他执行源码按 r16 原冻结文件继承。维护调度器：`/home/bfq/agenttx-maintenance/run_tb_network_resume_20261023.py`。

38 项针对性检查通过，涵盖网络附件/清理、原容器命令、故障修复不重复登记、复用未开始重跑、保留已成功最新结果、所有尝试累计账单、故障优先派发及原重跑协议。原始结果哈希清单及每次派发记录：`validation/tb-network-resume-20261023/`。

## 10:45 事务 hosts 基线修复及第二次续跑

第一条新完成的 create-bucket/Codex/不回滚虽然模型和容器正常退出、557866 token 账单完整，但官方测试失败包含 `Failed to resolve 'localhost'`。不能把这条简单归为纯求解失败，也没有把失败改成成功。检查发现旧 `prime_resolver` 只把 Docker 的 resolv.conf 复制进事务层，遗漏 Docker bind-mounted hosts；LocalStack 改动 DNS 后会暴露 localhost 解析缺失。

修复统一在模型启动前复制原容器的 resolv.conf 和 hosts 到事务基线，不向代理或测试提供答案；增加真实事务内 localhost 解析检查与基线哈希。原 create-bucket 镜像上 Codex 和 SWE-agent 的零模型事务/适配器/完成协议预检均通过，44 项针对性检查通过。证据：`validation/tb-api-connection-repair-20261023/hosts-baseline-{codex,sweagent}-verification.json`。

当前活动队列切为 `tb-dual-native-full-20261023-r19-hosts-resume`，服务 `agenttx-tb-dual-full-20261023-r19-hosts-resume.service`，维护代码 `/home/bfq/agenttx-maintenance/run_tb_hosts_resume_20261023.py`，审计 `validation/tb-hosts-resume-20261023/`。

r18 暂停新派发后，r19 接管其全部仍在运行的 worker，保持合计最多 8 路。r18/r17 暂停调度器的依赖链已登记，只有所有被接管任务结束才依次回收并交接全局锁；不盲目恢复旧 host-network 派发。r18 的所有结果和账单继续保留、计入累计成本。localhost 失败的修复尝试单独注册，按原连接事故时间优先处理；未开始的连接修复任务继续复用。r19 新派发单元采用同时修复网络隔离与 hosts 的新冻结源码；接管的旧 worker 保留其实际启动版本，不伪称已经修复。

后续汇报须分别列出 r19 新完成/验收通过、接管的旧尝试完成/通过、仍在执行和新的环境异常，不能把预检通过或模型调用成功当成任务成功。

## 11:01 原 LocalStack 服务入口修复

r19 的 3 个新完成 Codex/create-bucket 尝试均有完整账单，hosts 预检也通过，但实际验收仍报 localhost:4566 Connection refused，不能报告为环境已全部正常。只读核对原镜像发现它的 `docker-entrypoint.sh` 必须启动 localstack-supervisor；实验 worker 覆盖了 ENTRYPOINT，模型工具中临时启动的后台服务也无法保证跨工具/验收持续运行。

现对 create-bucket 运行原任务镜像、保留原 ENTRYPOINT 作为独立生命周期服务；worker 与官方验收共享其私有网络和原数据卷。服务启动检查只访问健康接口，不创建 sample-bucket 或设置 public ACL。零模型验证：原服务健康 200，S3 初始桶列表为空；两套真实适配器/事务预检通过；50 项针对性检查通过。证据：`validation/tb-api-connection-repair-20261023/original-localstack-verification.json`。

当前活动队列为 `tb-dual-native-full-20261023-r20-original-service-resume`，服务 `agenttx-tb-dual-full-20261023-r20-original-service-resume.service`，维护代码 `/home/bfq/agenttx-maintenance/run_tb_original_service_resume_20261023.py`，审计 `validation/tb-original-service-resume-20261023/`。

暂停 r19 派发时又有一个 create-bucket/SWE-agent/Ideal 任务进入，因此实际结束的是 **4** 条受已确认服务环境问题影响的旧尝试（不回滚/checkpoint/whole/Ideal）。其逐调用记录合计至少 665343 token，保留为不完整账单下界；可能仍有在途请求，不补零、不称完整、不计入算法失败或完整成本配对。原始中断结果及哈希单独审计，已有终态不覆盖。其他 4 路 hf-lora-adapter worker 接管继续；r20 补入 4 路新服务版任务，总并行仍为 8。

每个修复阶段至多为符合该阶段已审计环境故障条件的单元追加一次尝试，所有原始尝试/失败重跑/环境修复费用累计。旧环境失败不改成功；新任务最终是否通过仅依官方原始测试。

## 11:05 实际验收成功

r20 新版已完成 create-bucket 的 4 个实验单元，全部通过原始官方验收、账单全部完整：SWE-agent Clean 41885、SWE-agent 不回滚 55096、Codex Clean 233593、Codex 不回滚 321278 tokens，均含各自 main/replay，合计 651852。新增基础设施异常 0，队列自动补位维持 8 路。仅为同一题的 4 个单元，不能据此判断全量趋势。宿主机 8080 规则与最初只读观察相同。进度证据：validation/tb-original-service-resume-20261023/restart-success-progress.json。
