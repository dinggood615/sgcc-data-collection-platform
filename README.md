# 国网数据采集管理平台

面向国家电网电子商务平台“招标公告及投标邀请书”的专项采集与附件分析系统。项目固定集成国网站点，不提供自定义站点添加功能；保留定时任务、邮件、企业微信和备份迁移，并增加公告附件安全解析、项目/标包拆分、原文证据定位和文件版本去重。

> 本项目只处理公开信息或管理员通过合法授权渠道取得的文件，不绕过登录、验证码、访问限制、CA、加密压缩包或 `.sgcc` 文件保护。

## 国网专项流程

1. 通过国网公开 JSON 接口按目标日期逐页采集全部公告编号、标题、发布时间和详情地址。
2. 自动下载无需登录或验证的公开公告 ZIP，建立 SHA-256 版本记录并安全解析。
3. 对需要账号、CA 或官方工具的文件，由管理员合法下载或导出后在网页上传。
4. 安全解压 ZIP，拒绝路径穿越、符号链接、可执行文件、异常压缩比例、超大文件和加密包。
5. 解析 XLSX、DOCX、PDF、TXT 和 CSV；RAR/7Z 自动安全解包，旧版 Office 与 OFD 自动转换，扫描 PDF 自动 OCR。
6. 按公告、分标、项目和标包分别匹配关键词，结果显示来源文件、工作表行号或 PDF 页码及原文证据。

一个公告包含多个标包时，系统不会因为其中一个标包命中而把整批公告都判为相关。建议填写公告编号，稳定键由公告编号、分标编号、包号和项目/包名称共同组成。

## 当前支持

- 固定集成[国家电网招标公告及投标邀请书](https://ecp.sgcc.com.cn/ecp2.0/portal/#/list/list-spe/2018032600000014_5_2018032700291334)。
- 自动使用国网公开 JSON 接口，无需可视 Chrome、人工验证或重新识别。
- 服务端分页遍历、目标日期停止条件、温和访问间隔、公告 ID 去重和部分失败保留。
- 国网公开公告附件自动下载、文件指纹去重、ZIP 安全解压；手动导入仅作为受控补充入口。
- 兼容国网 ZIP 中央目录与本地文件头编码不一致的问题。
- Excel 工作表/行、Word 段落/表格、PDF 页码级证据定位；扫描 PDF 自动使用中文 OCR（带页数与超时限制）。
- 附件包内多个文件按 CPU 核数启用有上限的并行解析（最多 8 个工作线程，可用 `ATTACHMENT_PARSE_WORKERS` 调整）。
- 公开附件遇到临时网络或服务异常会自动退避重试；受邀权限附件自动识别并跳过，无文本附件自动归类，不再笼统标记为“需要人工检查”。重试次数可用 `ATTACHMENT_DOWNLOAD_RETRIES` 调整。
- 智能标包上下文：自动组合相邻的包号、项目名称、服务范围和全局分标信息，减少字段跨行导致的漏报。
- 关键词附近证据摘要与稳定键去重，避免只显示文档开头或重复候选标包。
- 标包命中通过公告编号关联采集结果，按“招标公告—分标—标包—附件原文位置”展示归属关系。
- 规则、OCR 与本地 Qwen3 混合分析：高置信度结果直接通过，中等置信度候选由 0.6B/1.7B 模型按需复核并保存置信度、类别和原文理由；模型不可用时自动退回规则结果。一键 Linux 安装会同时部署 llama.cpp 和模型，可用 `INSTALL_LOCAL_MODELS=0` 跳过约 1.8 GB 下载。
- 多平台共用的本机模型调度器统一串行化推理、互斥切换 0.6B/1.7B、缓存相同请求并在空闲后卸载模型，避免国网与新闻采集任务争抢端口和内存；接口仅监听 `127.0.0.1:8083`。
- 关键词、同义表达、业务对象与服务动作组合评分以及排除词降权。
- 首次启动内置并覆盖为信息化、数字化、软件实施、系统集成、运维及人力外包初筛词库；支持一键清除后自行维护。
- SQLite 去重、邮件日报、企业微信助手、数据库备份与完整迁移。
- Linux 原生安装及 Docker/群晖/飞牛 OS/OpenWrt 容器环境。

## 暂不自动处理

- 需要登录、短信、验证码、CA 或官方客户端才能取得的文件。
- 加密 ZIP、密码破解或 `.sgcc` 文件逆向解密。
- OCR 超过配置页数、识别超时或图片质量过低的页面会明确标记为需检查，不会误报为无相关项目。
- RAR、7Z 使用 7-Zip 安全预检后解包；旧版 `.xls/.doc` 通过 LibreOffice 转换，OFD 通过 easyofd 转为 PDF 后继续解析。加密或损坏文件仍会明确标记为需要检查。

这些限制会以“需检查”状态显示，不会被误判为“附件没有相关项目”。

## Linux 一键安装

适用于带 systemd 的 Ubuntu、Debian、RHEL/Rocky/AlmaLinux、Fedora、openSUSE 和 Arch Linux。Ubuntu/Debian 安装器同时安装 LibreOffice、Poppler、7-Zip 和中文 Tesseract。固定国网站点使用公开接口，不安装也不依赖可视 Chrome。

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/install-linux.sh | sudo bash
```

默认访问 `https://服务器IP:5555`，初始账号和密码均为 `admin`，请在首次登录后修改。
安装过程会提示输入域名和可选的证书通知邮箱；直接回车可继续使用自签名证书。需要企业微信回调和受信任 HTTPS 证书时，请先将域名 A/AAAA 记录解析到服务器，并放行 80、443 端口，也可以通过环境变量进行无人值守安装：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/install-linux.sh -o /tmp/install-sgcc.sh
sudo DOMAIN=sgcc.example.com LETSENCRYPT_EMAIL=admin@example.com bash /tmp/install-sgcc.sh
```

安装器会申请 Let's Encrypt 证书、启用 HTTP 到 HTTPS 跳转，并安装续期后的 Nginx 自动重载钩子。请将示例域名和邮箱替换为你的真实信息。

更新：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/update-linux.sh | sudo bash
```

更新脚本会自动识别 `sgcc-platform`/兼容旧服务名及实际后端端口；更新前创建 SQLite 一致性备份，更新失败时恢复原代码和服务。

一键卸载（会删除程序、数据库、浏览器会话和国网专项配置，不可恢复）：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/uninstall-linux.sh | sudo bash -s -- --yes
```

如需输入 `DELETE` 二次确认，请先下载再执行，避免 `curl` 管道占用标准输入：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/uninstall-linux.sh -o /tmp/uninstall-sgcc.sh
sudo bash /tmp/uninstall-sgcc.sh
```

## Docker 一键管理

```bash
# 安装
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/install-docker.sh | sh -s -- install

# 更新
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/install-docker.sh | sh -s -- update

# 卸载（交互确认）
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/install-docker.sh | sh -s -- uninstall
```

交互式安装会提示输入域名。填写后，Docker Compose 会启动 Caddy，在 80/443 端口自动申请和续期 HTTPS 证书；直接回车则保留 `http://设备IP:8000` 访问。使用域名前，请先将 DNS 解析到设备并放行 80、443 端口。无人值守安装也可以预设 `DOMAIN=sgcc.example.com`。

## 使用国网附件自动分析

1. 在“智能筛选规则”中添加关键词。
2. 点击“立即采集”或等待每日定时任务；平台自动采集指定日期并下载公开公告 ZIP。
3. 查看采集结果以及标包级命中结果、分数、原文证据和文件位置。
4. 只有网站要求合法登录、CA、受邀权限或自动下载失败时，才使用补充上传入口导入合法取得的 ZIP/XLSX/DOCX/PDF/TXT/CSV。

建议先用一个具有代表性的国网公开附件验证字段结构。不同省公司、批次和采购代理机构的 Excel 表头可能不同，后续可在不改变安全边界的前提下增加专用字段映射。

## 数据与安全

- 数据库默认位于 `/opt/sgcc-data-collection-platform/data/platform.sqlite3`。
- SMTP 授权码等敏感配置使用 `APP_SECRET` 派生密钥加密。
- 附件只在隔离临时目录解析，处理完成自动清理；数据库保存文件指纹、状态和结构化结果，不保存上传原件。
- 国网页面采用前端单页路由，平台只调用其页面自身使用的公开读取接口，不绕过访问控制。
- 仓库不包含站点账号、邮箱授权码、VPS 密码、Cookie、CA 文件或业务数据。

## 开发与测试

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q
```

健康检查：`GET /healthz`。

## 许可证与责任

请遵守目标网站服务条款、robots 约定、数据授权范围及适用法律法规。采集频率应保持温和；遇到拒绝访问、验证码或访问控制时应停止自动化并改用合法人工流程。
