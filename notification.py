import os
import smtplib
import logging
import itchat
import itchat.content
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from config import EMAIL_CONFIG, NOTIFICATION_CONFIG
import pandas as pd
import time
from wechat_utils import WeChatManager

class NotificationManager:
    def __init__(self):
        self.wechat_manager = None
        if NOTIFICATION_CONFIG['method'] in ['wechat', 'both']:
            self.wechat_manager = WeChatManager()

    def send_notification(self, subject, body, attachments=None):
        """发送通知，根据配置选择发送方式"""
        method = NOTIFICATION_CONFIG['method']

        if method == 'none':
            logging.info("Notifications are disabled; skipping notification delivery")
            return True

        success = True

        if method in ['email', 'both']:
            email_success = self._send_email(subject, body, attachments)
            success = success and email_success

        if method in ['wechat', 'both']:
            wechat_success = self._send_wechat(subject, body, attachments)
            success = success and wechat_success

        return success

    def _send_email(self, subject, body, attachments=None):
        """发送邮件通知"""
        try:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_CONFIG['sender_email']
            msg['To'] = EMAIL_CONFIG['recipient_email']
            msg['Subject'] = subject

            msg.attach(MIMEText(body, 'html'))

            if attachments:
                for filepath in attachments:
                    with open(filepath, 'rb') as f:
                        part = MIMEApplication(f.read(), Name=os.path.basename(filepath))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(filepath)}"'
                    msg.attach(part)

            with smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port']) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                logging.info("Attempting to login to Gmail...")
                server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
                logging.info("Login successful, sending email...")
                server.send_message(msg)
                
            logging.info(f"Email sent successfully: {subject}")
            return True
        except Exception as e:
            logging.error(f"Failed to send email: {str(e)}")
            logging.error(f"Email configuration used: server={EMAIL_CONFIG['smtp_server']}, port={EMAIL_CONFIG['smtp_port']}")
            return False

    def _format_wechat_message(self, subject, body, report_data=None):
        """格式化微信消息内容"""
        # 移除HTML标签
        text = self._html_to_text(body)
        
        # 提取和格式化关键信息
        lines = text.split('\n')
        formatted_lines = []
        
        # 添加标题
        formatted_lines.append(f"📊 {subject}")
        formatted_lines.append("=" * 30)
        
        # 处理正文
        current_section = ""
        trend_buffer = []  # 用于临时存储趋势数据
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 检测是否是新的部分
            if line.endswith(':'):
                # 如果有未处理的趋势数据，先处理它
                if trend_buffer:
                    formatted_lines.extend(self._format_trend_data(trend_buffer))
                    trend_buffer = []
                
                current_section = line
                formatted_lines.append(f"\n📌 {line}")
            elif line.startswith('Time Range:'):
                formatted_lines.append(f"🕒 {line}")
            elif line.startswith('Region:'):
                formatted_lines.append(f"🌍 {line}")
            elif line.startswith('Total keywords'):
                formatted_lines.append(f"📝 {line}")
            elif line.startswith('Successful'):
                formatted_lines.append(f"✅ {line}")
            elif line.startswith('Failed'):
                formatted_lines.append(f"❌ {line}")
            elif 'Growth:' in line or ('AI:' in line and 'Growth' in line):
                # 收集趋势数据进缓冲区
                trend_buffer.append(line)
            else:
                # 如果有未处理的趋势数据，先处理它
                if trend_buffer:
                    formatted_lines.extend(self._format_trend_data(trend_buffer))
                    trend_buffer = []
                formatted_lines.append(line)
        
        # 处理最后可能剩余的趋势数据
        if trend_buffer:
            formatted_lines.extend(self._format_trend_data(trend_buffer))
        
        if report_data is not None and isinstance(report_data, pd.DataFrame):
            formatted_lines.append("\n📌 详细报告:")
            
            for keyword in report_data['keyword'].unique():
                keyword_data = report_data[report_data['keyword'] == keyword]
                formatted_lines.append(f"\n🔍 {keyword}")
                
                for trend_type in ['rising', 'top']:
                    type_data = keyword_data[keyword_data['type'] == trend_type]
                    if not type_data.empty:
                        formatted_lines.append(f"  {'↗️ 上升趋势' if trend_type == 'rising' else '⭐ 热门趋势'}:")
                        for _, row in type_data.iterrows():
                            formatted_lines.append(f"    • {row['related_keywords']} ({row['value']})")
        
        return '\n'.join(formatted_lines)

    def _format_trend_data(self, trend_lines):
        """格式化趋势数据
        
        Args:
            trend_lines: 包含趋势数据的行列表
        
        Returns:
            格式化后的行列表
        """
        formatted_lines = []
        current_keyword = None
        current_data = {}
        
        for line in trend_lines:
            try:
                # 处理包含完整信息的单行
                if ':' in line and 'Growth:' in line:
                    parts = line.split(':', 1)
                    keyword = parts[0].strip()
                    rest = parts[1]
                    
                    # 尝试分离相关查询和增长率
                    if '(Growth:' in rest:
                        query, growth = rest.split('(Growth:', 1)
                        growth = growth.strip('() ')
                    else:
                        # 如果格式不标准，尝试其他分割方式
                        rest_parts = rest.split('Growth:', 1)
                        if len(rest_parts) == 2:
                            query = rest_parts[0]
                            growth = rest_parts[1].strip('() ')
                        else:
                            query = rest
                            growth = 'N/A'
                    
                    formatted_lines.append(f"\n↗️ 关键词: {keyword}")
                    formatted_lines.append(f"   相关查询: {query.strip()}")
                    formatted_lines.append(f"   增长幅度: {growth}")
                else:
                    # 处理其他格式的行
                    formatted_lines.append(f"   {line}")
            except Exception as e:
                logging.warning(f"Error formatting trend line '{line}': {str(e)}")
                formatted_lines.append(f"   {line}")
        
        return formatted_lines

    def _send_wechat_message_in_chunks(self, message, receiver_id, chunk_size=2000):
        """分段发送微信消息"""
        lines = message.split('\n')
        current_chunk = []
        current_length = 0
        
        for line in lines:
            line_length = len(line) + 1  # +1 for newline
            
            if current_length + line_length > chunk_size and current_chunk:
                chunk_text = '\n'.join(current_chunk)
                if not self.wechat_manager.send_message(chunk_text, receiver_id):
                    raise Exception("Failed to send message chunk")
                time.sleep(0.5)
                current_chunk = []
                current_length = 0
            
            if line_length > chunk_size:
                if current_chunk:
                    chunk_text = '\n'.join(current_chunk)
                    if not self.wechat_manager.send_message(chunk_text, receiver_id):
                        raise Exception("Failed to send message chunk")
                    time.sleep(0.5)
                    current_chunk = []
                    current_length = 0
                
                for i in range(0, len(line), chunk_size):
                    chunk = line[i:i + chunk_size]
                    if not self.wechat_manager.send_message(chunk, receiver_id):
                        raise Exception("Failed to send message chunk")
                    time.sleep(0.5)
            else:
                current_chunk.append(line)
                current_length += line_length
        
        if current_chunk:
            chunk_text = '\n'.join(current_chunk)
            if not self.wechat_manager.send_message(chunk_text, receiver_id):
                raise Exception("Failed to send final message chunk")

    def _send_wechat(self, subject, body, attachments=None):
        """发送微信通知"""
        if not self.wechat_manager:
            logging.error("WeChat manager not initialized")
            return False

        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                if not self.wechat_manager.ensure_login():
                    raise Exception("Failed to ensure WeChat connection")

                receiver_name = NOTIFICATION_CONFIG['wechat_receiver']
                receiver_id = self.wechat_manager.get_user_id(receiver_name)
                if not receiver_id:
                    raise Exception(f"Cannot find receiver: {receiver_name}")
                
                report_data = None
                if attachments and any(f.endswith('.csv') for f in attachments):
                    csv_file = next(f for f in attachments if f.endswith('.csv'))
                    try:
                        report_data = pd.read_csv(csv_file)
                    except Exception as e:
                        logging.warning(f"Failed to read report CSV file: {str(e)}")
                
                message = self._format_wechat_message(subject, body, report_data)
                self._send_wechat_message_in_chunks(message, receiver_id)
                
                if attachments:
                    for filepath in attachments:
                        if not filepath.endswith('.csv'):
                            file_message = f"\n📎 正在发送文件: {os.path.basename(filepath)}"
                            if not self.wechat_manager.send_message(file_message, receiver_id):
                                raise Exception("Failed to send file message")
                            itchat.send_file(filepath, toUserName=receiver_id)
                
                logging.info(f"WeChat message sent successfully: {subject}")
                return True
                
            except Exception as e:
                retry_count += 1
                error_msg = f"Failed to send WeChat message (attempt {retry_count}/{max_retries}): {str(e)}"
                if retry_count < max_retries:
                    logging.warning(error_msg + " Retrying...")
                    time.sleep(5)
                else:
                    logging.error(error_msg)
                    return False
        
        return False

    def _html_to_text(self, html):
        """简单的HTML到纯文本转换"""
        import re
        text = re.sub('<[^<]+?>', '', html)
        return text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
