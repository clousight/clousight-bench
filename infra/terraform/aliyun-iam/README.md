# Terraform module — Aliyun RAM provisioning identity

**设计原则**：一个账号一个 RAM 用户（`clousight-bench`），每个测评云产品挂一条
自定义策略（`ClousightBench-<product>`）。用户不膨胀，权限按产品隔离，审计清晰。

```
clousight-bench (RAM user)
  ├── ClousightBench-AgentRun   ← CreateRuntime / GetRuntime / DeleteRuntime / OSS artifact
  ├── ClousightBench-EMR        ← (future) EMR cluster lifecycle
  └── ClousightBench-RDS        ← (future) RDS instance lifecycle
```

## Quick start

```bash
# 1. 配置管理员凭证（用于创建 RAM 用户，不是 benchmark 用的凭证）
export ALICLOUD_ACCESS_KEY="<admin-key-id>"
export ALICLOUD_SECRET_KEY="<admin-key-secret>"
export ALICLOUD_REGION="cn-hangzhou"

# 2. 配置（bucket 可不填，自动生成）
cd infra/terraform/aliyun-iam
cp terraform.tfvars.example terraform.tfvars
# 按需编辑 terraform.tfvars（region / enabled_products / enable_data_plane）

# 3. 创建资源
terraform init
terraform apply

# 4. 生成 csbench 运行配置（bucket 名已自动填入）
terraform output -raw csbench_config > ../../agent-runtime-aliyun.local.yaml

# 5. 切换到 benchmark 子用户凭证
eval "$(terraform output -raw export_commands)"

# 6. 预检 + 开跑
csbench doctor --domain agent-runtime --platform aliyun-agentrun \
  --task T0.1 --config agent-runtime-aliyun.local.yaml
csbench run --domain agent-runtime --task T0.1 --platform aliyun-agentrun \
  --config agent-runtime-aliyun.local.yaml --results results/ --allow-live
```

## 幂等性

| 资源 | 已存在时的行为 |
|------|--------------|
| RAM 用户 | data source 查到 → 跳过创建，复用 |
| 产品策略 | `force = true` → 内容有变则更新，否则不动 |
| 策略附加 | Terraform state 追踪 → 已附加则不重复操作 |
| AccessKey | 创建一次，后续 apply 不重建 |

> 如果用户或附加关系是**手工创建**、不在 state 里的，用 `terraform import` 导入：
> ```bash
> terraform import alicloud_ram_user.bench[0] clousight-bench
> terraform import 'alicloud_ram_user_policy_attachment.product["AgentRun"]' \
>   clousight-bench:Custom:ClousightBench-AgentRun
> ```

## 扩展新产品

1. 在 `policies/` 目录下新建 `<product>.json.tpl`（参考 `agentrun.json.tpl`）
2. 在 `terraform.tfvars` 的 `products` map 里加一条：
   ```hcl
   EMR = templatefile("policies/emr.json.tpl", { ... })
   ```
3. `terraform apply` — 只会新增 `ClousightBench-EMR` 策略并附加，不影响其他产品

## 数据面权限（T1–T5）

AgentRun 控制面 smoke 完成后，把 `enable_data_plane = true` 加进 tfvars 重新
apply，策略会自动更新，添加 `InvokeRuntime / *Memory / ActivateTemplateMCP`。

## 探针 (Probe) 资源

> 背景:探针下沉(probe-sink)把数据面压测搬进目标区域的一台 ECI,消除本机网络/系统代理对延迟指标的污染。
> 完整架构见 [`docs/wiki/10-probe-sink.md`](../../../docs/wiki/10-probe-sink.md)。本节只讲它依赖的基础设施。

`enable_probe = true` 时本模块额外创建：

| 资源 | 说明 |
|------|------|
| `alicloud_ram_role.eci_probe` | ECI 实例 RAM 角色（无静态凭证，探针容器自动获取 STS token） |
| `alicloud_ram_policy.eci_probe_ops` | 赋予 benchmark 用户 ECI 容器组生命周期权限（Create/Describe/Delete）；`ram:PassRole` 仅限 `eci_probe` 角色 + ECI 服务（`acs:Service = eci.aliyuncs.com`），防止权限提升 |
| OSS lifecycle rule `expire-telemetry` | 30 天后删除 `clousight-bench/telemetry/` 前缀的对象 |
| OSS lifecycle rule `expire-session` | 7 天后删除 `clousight-bench/state/` 前缀的对象 |
| OSS lifecycle rule `abort-multipart` | 1 天后中止 `clousight-bench/` 下未完成的分片上传 |

Terraform `apply` 后执行：

```bash
# 输出 ECI probe 角色名 + 网络 ID
terraform output eci_probe_role_name
terraform output probe_vswitch_id
terraform output probe_security_group_id

# 更新 csbench 运行配置（包含 eci_probe_role / eci_vswitch_id / eci_security_group_id）
terraform output -raw csbench_config > ../../agent-runtime-aliyun.local.yaml
```

### ARMS 追踪数据保留期（Phase B）

ARMS 追踪保留期**不是** Terraform 资源（provider v1.220 尚无对应 resource），
由 `set-arms-retention.sh` 脚本通过 ARMS OpenAPI 设置，幂等可重复执行。

**仅在 Phase B（有真实云账号凭证）时运行：**

```bash
# 需要主账号 ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY（非 benchmark 子用户）
export ALICLOUD_REGION="cn-hangzhou"
export ARMS_TRACE_APP_NAME="clousight-bench"   # 若不同请修改

cd infra/terraform/aliyun-iam
./set-arms-retention.sh 15   # 设为 15 天
```

> **PHASE-B-VERIFY**：脚本中 `SearchTraceAppByName` / `UpdateTraceAppConfig` /
> `tracesDataRetention` 等 ARMS OpenAPI action 和参数名需在 Phase B 对照真实
> ARMS 控制台 / API Explorer 确认后才执行；保留天数 15 天为已审定决策。

## AccessKey 安全说明

Secret 存在 Terraform state 文件里。团队使用时配置加密远端 backend（OSS + 状态锁）；
个人本机使用 local state 可接受，但不要提交。记录 Secret 后可从 state 删除：
```bash
terraform state rm alicloud_ram_access_key.bench
```
