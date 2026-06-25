# astrbot_plugin_napcat_offline_notice

一个给 AstrBot 用的 NapCat 掉线通知插件。

它会持续监控 AstrBot 中的 `aiocqhttp / OneBot v11` 平台实例，一旦发现 NapCat 从在线变成断开，就主动把提醒消息发送到你指定的其他会话里，比如微信、企业微信、Telegram、Discord 等。  
如果开启了恢复通知，它也会在重新连上之后补发一条“已恢复”的提醒。

## 为什么会有这个插件

很多人会把 AstrBot 同时接到多个平台：

- QQ 侧通过 NapCat 接入
- 其他平台继续走微信、企业微信、Telegram、Discord 等适配器

问题是，一旦 NapCat 被踢下线、断网、进程退出，QQ 侧就会静默失联，往往要过一阵子才会发现。  
这个插件就是为了解决这个问题：让 AstrBot 自己在其他还活着的会话里告诉你，“QQ 那边掉了”。

## 功能特性

- 监控全部或指定的 `aiocqhttp` 平台实例
- NapCat 掉线后主动通知指定会话
- NapCat 恢复连接后可选通知
- 支持在目标会话中直接执行命令完成绑定
- 支持给绑定会话添加备注，方便管理
- 优先使用目标会话当前的模型和人格生成通知文案
- 自带冷却时间，避免重复刷屏

## 适用场景

- 你用 NapCat 把 QQ 接进了 AstrBot
- 你还把 AstrBot 接到了其他平台
- 你希望 NapCat 掉线时，能第一时间在别的平台收到提醒
- 你希望提醒文案更像当前会话平时的说话风格，而不是死板系统通知

## 效果说明

这个插件检测到的是“连接状态从在线变成离线”。

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

### 1. 启用插件

在 AstrBot 插件管理中启用本插件。

### 2. 配置监控范围

插件配置中的 `target_platform_ids`：

- 留空：监控全部 `aiocqhttp / OneBot v11` 实例
- 填值：只监控指定平台 ID

如果你只有一个 NapCat，通常可以直接留空。

### 3. 绑定接收通知的会话

到你想接收通知的目标对话里发送：

```text
/napcat_notice bind
```

如果想加一个备注：

```text
/napcat_notice bind 微信提醒
```

### 4. 发送测试通知

```text
/napcat_notice test
```

如果测试通知能正常收到，说明整条通知链路已经打通。

## 命令说明

| 命令 | 说明 |
| --- | --- |
| `/napcat_notice bind [备注]` | 将当前会话绑定为通知目标 |
| `/napcat_notice unbind` | 解绑当前会话 |
| `/napcat_notice list` | 查看所有已绑定会话 |
| `/napcat_notice status` | 查看当前监控状态 |
| `/napcat_notice test` | 向所有已绑定会话发送测试通知 |

说明：

- 这些命令默认要求管理员权限
- 绑定操作建议直接在目标会话里执行，这样不需要手填复杂会话 ID

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

## 推荐使用方式

推荐按下面的顺序配置：

1. 先留空 `target_platform_ids`
2. 在目标会话里执行 `/napcat_notice bind`
3. 执行 `/napcat_notice test`
4. 确认通知正常后，再决定是否要限定某一个平台 ID

## 注意事项

- 目标会话所在平台必须支持主动消息发送
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

当前版本：`0.1.0`

这是首个可用版本，已具备：

- 掉线监控
- 恢复通知
- 目标会话绑定
- 测试命令
- LLM 文案生成
- 固定模板回退

## 后续可扩展方向

- 支持 WebUI 可视化管理通知目标
- 支持按平台实例分别设置不同通知会话
- 支持更详细的异常原因分类
- 支持通知历史记录
