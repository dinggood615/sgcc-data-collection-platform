# 国网数据采集管理平台

面向国家电网电子商务平台公开招标公告及投标邀请书的专项采集与附件分析系统。项目从[数据采集管理平台](https://github.com/dinggood615/data-collection-management-platform)独立演进，保留站点管理、定时任务、邮件、企业微信、备份迁移和可视浏览器能力，并增加公告附件安全解析、项目/标包拆分、原文证据定位和文件版本去重。

> 本项目只处理公开信息或管理员通过合法授权渠道取得的文件，不绕过登录、验证码、访问限制、CA、加密压缩包或 `.sgcc` 文件保护。

## 国网专项流程

1. 采集公开公告编号、标题、发布时间、详情地址和附件元数据。
2. 对无需登录或验证的公开附件建立下载与 SHA-256 版本记录。
3. 对需要账号、CA 或官方工具的文件，由管理员合法下载或导出后在网页上传。
4. 安全解压 ZIP，拒绝路径穿越、符号链接、可执行文件、异常压缩比例、超大文件和加密包。
5. 解析 XLSX、DOCX、文本 PDF、TXT 和 CSV；旧版 Office、OFD、扫描件会明确标记为需要转换或 OCR。
6. 按公告、分标、项目和标包分别匹配关键词，结果显示来源文件、工作表行号或 PDF 页码及原文证据。

一个公告包含多个标包时，系统不会因为其中一个标包命中而把整批公告都判为相关。建议填写公告编号，稳定键由公告编号、分标编号、包号和项目/包名称共同组成。

## 当前支持

- Angular/Vue/React 等动态公告页面的可视 Chrome 辅助识别。
- Scrapling 静态 DOM 自适应和公开 JSON 接口识别。
- 国网附件手动导入、文件指纹去重、ZIP 安全解压。
- Excel 工作表/行、Word 段落/表格、PDF 页码级证据定位。
- 关键词、同义表达、业务对象与服务动作组合评分以及排除词降权。
- SQLite 去重、邮件日报、企业微信助手、数据库备份与完整迁移。
- Linux 原生安装及 Docker/群晖/飞牛 OS/OpenWrt 容器环境。

## 暂不自动处理

- 需要登录、短信、验证码、CA 或官方客户端才能取得的文件。
- 加密 ZIP、密码破解或 `.sgcc` 文件逆向解密。
- 扫描 PDF 的 OCR 尚未自动接入；界面会显示“可能是扫描件，需要 OCR”。
- RAR、7Z、OFD 和旧版 `.xls/.doc` 当前需先通过可信工具转换为 ZIP/XLSX/DOCX/PDF。

这些限制会以“需检查”状态显示，不会被误判为“附件没有相关项目”。

## Linux 一键安装

适用于带 systemd 的 Ubuntu、Debian、RHEL/Rocky/AlmaLinux、Fedora、openSUSE 和 Arch Linux。Ubuntu/Debian 安装器同时安装 LibreOffice、Poppler、7-Zip、中文 Tesseract 和可视 Chrome 环境。

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/install-linux.sh | sudo bash
```

默认访问 `https://服务器IP:5555`，初始账号为 `admin / admin`，首次登录后请立即修改密码。

更新：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/update-linux.sh | sudo bash
```

卸载：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/sgcc-data-collection-platform/main/uninstall-linux.sh | sudo bash
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

## 使用国网附件分析

1. 在“智能筛选规则”中添加关键词。
2. 打开“国网公告附件分析”。
3. 填写公告编号和原公告地址。
4. 上传公开下载或合法取得的 ZIP/XLSX/DOCX/PDF/TXT/CSV。
5. 查看标包级命中结果、分数、原文证据和文件位置。

建议先用一个具有代表性的国网公开附件验证字段结构。不同省公司、批次和采购代理机构的 Excel 表头可能不同，后续可在不改变安全边界的前提下增加专用字段映射。

## 数据与安全

- 数据库默认位于 `/opt/sgcc-data-collection-platform/data/platform.sqlite3`。
- SMTP 授权码等敏感配置使用 `APP_SECRET` 派生密钥加密。
- 附件只在隔离临时目录解析，处理完成自动清理；数据库保存文件指纹、状态和结构化结果，不保存上传原件。
- noVNC 与 Chrome 调试端口不直接暴露公网，由已登录的管理页面代理访问。
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
