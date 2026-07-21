# astrbot_plugin_napcat_offline_notice

一个给 AstrBot 用的 NapCat 掉线通知插件。

它会持续监控 AstrBot 中的 `aiocqhttp / OneBot v11` 平台实例，一旦发现 NapCat 从在线变成断开，就会自动向 AstrBot 配置里的所有管理员发送通知。通知会使用管理员最近和 AstrBot 对话过的会话进行推送，并优先使用该会话当前的模型与人格生成提醒文案。

## 为什么会有这个插件

很多人会把 AstrBot 同时接到多个平台：

- QQ 侧通过 NapCat 接入
- 其他平台继续走微信、企业微信、Telegram、Discord 等适配器

问题是，一旦 NapCat 被踢下线、断网、进程退出，QQ 侧就会静默失联，往往要过一阵子才会发现。  
这个插件就是为了解决这个问题：让 AstrBot 自己在其他还活着的会话里告诉你，"QQ 那边掉了"。

## 0.3.0 跨平台投递修复

- 每个管理员会分别保留微信、WebChat、企业微信、Telegram 等平台会话，后来的 QQ 消息不会再覆盖其他平台。
- 管理员身份以 AstrBot 的 `event.is_admin()` 为准，不再要求 sender ID 再次命中 `admins_id`。
- NapCat 掉线时排除所有 `aiocqhttp` 会话，只向仍存在的其他平台投递。
- 发送失败不会进入冷却；只有实际送达后才记录冷却时间。
- 默认持续重试 5 分钟，管理员在此期间从其他平台发消息后，新会话会自动加入下一轮投递。
- 旧版单会话存储会自动迁移成多平台格式。
## 0.2.0 版本新特性

- **当时的自动管理员推送**：0.2.0 只保存单个最近会话；该行为已在 0.3.0 改为多平台会话，并恢复显式 bind 兜底
- **轮询重试机制**：通知发送失败时会自动重试，直到至少推送给一个管理员
- **本地测试功能**：新增 `fake_offline` / `fake_online` 命令，无需真实断开 NapCat 也能测试推送逻辑
- **简化使用流程**：管理员只要先和 AstrBot 说句话，插件就会记住会话并用于后续通知

## 功能特性

- 监控全部或指定的 `aiocqhttp` 平台实例
- 自动向所有 AstrBot 管理员推送通知
- 分别保留管理员在每个平台的最近会话，QQ 消息不会覆盖微信等其他平台
- 支持轮询重试直到至少一个管理员收到通知
- NapCat 恢复连接后可选通知
- 优先使用目标会话当前的模型和人格生成通知文案
- 自带冷却时间，避免重复刷屏
- 支持假装离线/恢复，用于本地测试

## 适用场景

- 你用 NapCat 把 QQ 接进了 AstrBot
- 你还把 AstrBot 接到了其他平台
- 你在 AstrBot 配置里设置了 `admins_id`（管理员列表）
- 你希望 NapCat 掉线时，能第一时间在别的平台收到提醒
- 你希望提醒文案更像当前会话平时的说话风格，而不是死板系统通知

## 效果说明

这个插件检测到的是"连接状态从在线变成离线"。

也就是说，它能可靠判断：

- NapCat 已经断开
- QQ 侧消息现在大概率收不到了

但它不能 100% 精确区分：

- 是被踢下线
- 还是网络断开
- 还是 NapCat 进程退出

因此插件在提醒文案上会采用谨慎描述，例如：

- `NapCat 已断开连接，可能是被踢下线`
- `QQ 侧消息暂时不可用`

这比伪造一个并不确定的具体原因更稳妥。

## 安装

把插件目录放到 AstrBot 的插件目录中：

```text
data/plugins/astrbot_plugin_napcat_offline_notice
```

然后在 AstrBot WebUI 的插件管理中启用或重载插件。

## 快速开始

### 1. 确认 AstrBot 配置了 admins_id

在 AstrBot 配置中，确保你已经设置了 `admins_id`（通常在 data/config 目录下的配置文件中），这是插件识别管理员的依据。

### 2. 启用插件

在 AstrBot 插件管理中启用本插件。

### 3. 让插件记住你的会话

作为管理员，随便给 AstrBot 发一句话（任意平台都可以），插件会自动记住该会话用于后续推送。

### 4. 发送测试通知

```text
/napcat_notice test
```

如果测试通知能正常收到，说明整条通知链路已经打通。

### 5. 本地测试（假装离线）

```text
/napcat_notice fake_offline
```

这样插件会在下次轮询时认为 NapCat 已离线并触发通知推送，无需真实断开 NapCat。

恢复真实状态：

```text
/napcat_notice fake_online
```

## 命令说明

| 命令 | 说明 |
| --- | --- |
| `/napcat_notice list` | 查看当前配置的管理员和插件记住的推送会话 |
| `/napcat_notice status` | 查看当前监控状态 |
| `/napcat_notice test` | 向所有已知管理员会话发送测试通知 |
| `/napcat_notice fake_offline [平台ID]` | 假装指定或所有平台离线（测试用） |
| `/napcat_notice fake_online [平台ID]` | 取消假装离线，恢复真实状态 |
| `/napcat_notice bind` | 显式绑定当前平台会话，推荐在接收通知的其他平台执行 |
| `/napcat_notice unbind` | 只解绑当前平台会话，不影响其他平台 |

说明：

- 这些命令默认要求管理员权限
- 管理员只要和 AstrBot 说过话，插件就会自动记住该会话

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `target_platform_ids` | 空 | 监控的 `aiocqhttp` 平台 ID，留空表示监控全部 |
| `poll_interval_seconds` | `5` | 轮询连接状态的间隔秒数 |
| `offline_cooldown_seconds` | `600` | 同一平台相同状态的重复通知冷却时间 |
| `notify_recovery` | `true` | 恢复连接后是否也发送通知 |
| `use_llm` | `true` | 是否优先使用当前会话模型与人格生成通知文案 |
| `fallback_offline_template` | 内置模板 | 掉线时的回退通知模板 |
| `fallback_recovery_template` | 内置模板 | 恢复时的回退通知模板 |
| `llm_prompt_template` | 内置模板 | LLM 生成通知文案时使用的提示词模板 |

## 文案生成逻辑

如果 `use_llm` 开启，插件会尝试：

1. 获取目标会话当前正在使用的 provider
2. 解析该会话当前生效的人格 prompt
3. 让模型生成一条简短自然的提醒消息

如果出现以下情况：

- 当前会话没有可用模型
- provider 不可用
- LLM 调用失败
- 人格解析失败

插件会自动回退到固定模板文案，不会因为模型异常导致整条提醒丢失。

## 工作原理

插件会轮询 AstrBot 当前 `aiocqhttp` 适配器内部的连接数：

- 连接数 `> 0`：视为在线
- 连接数 `= 0`：视为离线

当状态发生变化时：

- 在线 -> 离线：发送掉线通知
- 离线 -> 在线：如果启用了恢复通知，则发送恢复通知

为了避免刷屏，插件会对同一平台的相同状态应用冷却时间。

通知策略：

1. 读取 AstrBot 全局配置里的 `admins_id`
2. 加载管理员在所有平台保存的会话，并排除掉线的 NapCat/QQ 会话
3. 优先向仍在线的非 QQ 平台逐一推送；失败时默认持续重试 5 分钟
4. 每次重试前重新加载会话列表，期间新增的微信/WebChat 等会话会立即加入投递
5. 只有实际发送成功后才写入冷却时间，失败不会压住后续通知

## 推荐使用方式

推荐按下面的顺序配置：

1. 确保 AstrBot 配置里有 `admins_id`
2. 先留空 `target_platform_ids`
3. 以管理员身份和 AstrBot 说一句话
4. 执行 `/napcat_notice test` 确认推送链路正常
5. 执行 `/napcat_notice fake_offline` 测试假装离线逻辑
6. 确认通知正常后，再决定是否要限定某一个平台 ID

## 注意事项

- 管理员会话所在平台必须支持主动消息发送
- 如果管理员会话本身就在同一个 NapCat / OneBot v11 平台实例上，那么它掉线时无法靠自己给自己发通知
- 个人微信 `weixin_oc` 的主动发送依赖最近会话上下文；如果长期没说过话，`/napcat_notice test` 也可能失败，需要先给 AstrBot 发一条真实消息刷新上下文
- 如果你配置了多个 NapCat / OneBot v11 实例，建议为不同平台实例设置清晰的 `id`
- 如果未来 AstrBot 内部 `aiocqhttp` 连接实现发生变化，本插件的监控逻辑可能需要做适配更新

## 仓库结构

```text
astrbot_plugin_napcat_offline_notice/
├─ __init__.py
├─ _conf_schema.json
├─ main.py
├─ metadata.yaml
└─ README.md
```

## 版本状态

当前版本：`0.3.0`

主要更新：

- 改为自动向所有 AstrBot 管理员推送通知
- 新增自动记住管理员最近会话的功能
- 新增轮询重试机制
- 新增 `fake_offline` / `fake_online` 本地测试命令
- 支持自动记录以及显式 bind/unbind 两种会话管理方式
