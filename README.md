# Clash bundle generator

这个目录里的脚本会从你的 Clash 订阅链接下载原始配置，然后重组为一个顶层更精简的配置集，结构上接近你给的 `example.yaml`：顶层只保留基础设置、`proxy-providers`、`proxy-groups`、`rule-providers` 和少量规则，真正的节点和规则内容放进独立文件里。

## 输出文件

- `dist/managed.yaml`：最终给 Clash 订阅使用的顶层配置
- `dist/proxy-providers/my-subscription.yaml`：节点 provider 文件
- `dist/ruleset/source-direct.yaml`：原始配置里直连规则的规则集
- `dist/ruleset/source-proxy.yaml`：原始配置里代理规则的规则集
- `dist/ruleset/custom-direct.yaml`：你自定义的直连规则集
- `dist/ruleset/custom-proxy.yaml`：你自定义的代理规则集
- `dist/source-rules-direct.yaml`：从原始订阅拆出的直连规则明细
- `dist/source-rules-proxy.yaml`：从原始订阅拆出的代理规则明细
- `dist/custom-rules.txt`：你自己的自定义规则源文件
- `dist/base.yaml`：从原始订阅中保留下来的基础配置
- `dist/build-info.json`：本次生成的摘要信息

## 用法

先安装依赖：

```bash
/usr/bin/python -m pip install -r requirements.txt
```

然后复制 [.env.example](.env.example) 为 `.env`，按你的服务器地址修改其中的值。脚本默认会读取当前目录下的 `.env` 文件，所以平时直接运行即可：

```bash
/usr/bin/python clash_bundle.py
```

如果你想临时覆盖某个值，也可以继续传命令行参数，例如：

```bash
/usr/bin/python clash_bundle.py --public-base-url https://your-domain.example/clash
```

第一次运行时，如果 `custom-rules.txt` 不存在，脚本会自动创建一个模板。

## 定时运行

可以把上面的命令放进 `cron`，例如每 6 小时执行一次：

```cron
0 */6 * * * /usr/bin/python /home/harry/clash-conf/clash_bundle.py
```

## 规则方式

`custom-rules.txt` 里的规则建议写成标准 Clash 规则，并明确目标是 `DIRECT` 还是 `PROXY`。脚本会自动把它们分到对应的规则集文件里，再由顶层 `managed.yaml` 用 `RULE-SET` 引用。