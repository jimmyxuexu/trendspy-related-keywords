# Google Trends 监控工具

这是一个用于监控 Google Trends 数据的自动化工具。它可以定期查询指定关键词的趋势数据，生成报告，并通过邮件、微信或 GitHub Pages 静态网站展示结果。

## 功能特点

- 🔄 每日自动查询多个关键词的趋势数据
- 📊 生成详细的数据报告，包括上升趋势和热门趋势
- 📱 支持多种通知方式（邮件、微信、关闭通知）
- ⚡ 智能的请求频率控制，避免触发限制
- 📈 监控关键词的增长趋势，当超过阈值时发送提醒
- 📁 按日期组织数据文件，方便查询历史记录
- 🌐 支持 GitHub Actions 定时生成静态报表并发布到 GitHub Pages

## 安装说明

1. 克隆仓库：
```bash
git clone [repository-url]
cd [repository-name]
```

2. 创建并激活虚拟环境（推荐）：
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
.\venv\Scripts\activate  # Windows
```

3. 安装依赖：
```bash
pip install -r requirements.txt
```

## 配置说明

1. 复制环境变量示例文件：
```bash
cp .env.example .env
```

2. 编辑 `.env` 文件，配置以下信息：
```
# 通知方式
TRENDS_NOTIFICATION_METHOD=none

# 邮件配置（使用Gmail时）
TRENDS_SMTP_SERVER=smtp.gmail.com
TRENDS_SMTP_PORT=587
TRENDS_SENDER_EMAIL=your-email@gmail.com
TRENDS_SENDER_PASSWORD=your-app-password
TRENDS_RECIPIENT_EMAIL=recipient@example.com

# 微信配置
TRENDS_WECHAT_RECEIVER=filehelper  # 接收者的微信号或备注名
```

3. 编辑 `config.py` 文件，根据需要修改：
- 监控的关键词列表
- 查询时间范围
- 数据采集频率
- 报告格式
- 其他配置项

## 使用说明

### 主程序

1. 测试模式运行：
```bash
python trends_monitor.py --test
```

2. 使用指定关键词测试：
```bash
python trends_monitor.py --test --keywords "Python" "AI"
```

3. 正常运行（定时任务模式）：
```bash
python trends_monitor.py
```

### GitHub Actions + Pages

仓库已包含 [publish-pages.yml](.github/workflows/publish-pages.yml) 工作流，适合免费部署为“定时抓取 + 网页查看”的方案。

1. 将项目推到 GitHub 仓库。
2. 在仓库 `Settings -> Pages` 中选择：
   `Deploy from a branch`
   `Branch: main`
   `Folder: /docs`
3. 在 `Actions` 页面手动运行一次 `Publish Trends Site`。
4. 之后工作流会每天自动执行一次。

默认计划时间是 `15:15 UTC`，对应中国时间 `23:15`。如果你想改时间，编辑 [.github/workflows/publish-pages.yml](.github/workflows/publish-pages.yml) 里的 `cron` 即可。

GitHub Pages 发布后，你可以通过仓库提供的 Pages 地址随时查看：
- 最新运行摘要
- 每个关键词的 `top` / `rising` 查询
- 高增长告警
- 历史运行列表
- 对应日期的 CSV 和 JSON 原始文件

### 微信工具

使用微信通知功能前，需要先运行微信工具来获取正确的接收者ID：

```bash
python wechat_utils.py
```

微信工具提供以下功能：
- 搜索联系人
- 搜索群聊
- 显示所有联系人
- 显示所有群聊

## 数据输出

1. 数据文件
- 每日数据保存在 `data_YYYYMMDD` 目录下
- JSON 格式的原始数据
- CSV 格式的汇总报告
- 静态网站输出在 `docs/` 目录，适合 GitHub Pages

2. 通知内容
- 每日趋势报告
- 高增长趋势提醒（当增长超过阈值时）
- 错误通知（当发生异常时）

## 注意事项

1. Gmail 配置
- 需要开启两步验证
- 需要生成应用专用密码
- 详细说明：[Gmail 应用密码设置](https://support.google.com/accounts/answer/185833)

2. 微信配置
- 首次使用需要扫码登录
- 登录状态会保持一段时间
- 建议使用文件传输助手（filehelper）进行测试

3. 请求限制
- 已实现智能的请求频率控制
- 建议不要设置过多关键词
- 批量处理时会自动添加延迟

## 常见问题

1. 邮件发送失败
- 检查 SMTP 配置是否正确
- 确认应用密码是否正确
- 检查网络连接状态

2. 微信登录问题
- 确保微信版本兼容
- 尝试重新扫码登录
- 检查防火墙设置

3. 数据采集问题
- 检查网络连接
- 确认关键词格式正确
- 查看日志文件获取详细错误信息

## 许可证

[您的许可证类型]

## 贡献指南

欢迎提交 Issue 和 Pull Request！ 
