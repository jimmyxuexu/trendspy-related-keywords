import os
import itchat
import logging
from tabulate import tabulate
import time
import threading
import atexit
from typing import Optional
from config import NOTIFICATION_CONFIG

class WeChatManager:
    _instance = None
    _lock = threading.Lock()
    _itchat_pkl = 'itchat.pkl'  # itchat默认的缓存文件名
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(WeChatManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self._logged_in = False
        self._login_lock = threading.Lock()
        self._is_shutting_down = False
        
        # 检查是否需要微信功能
        self._need_wechat = NOTIFICATION_CONFIG['method'] in ['wechat', 'both']
        
        # 只有在需要微信功能时才检查itchat是否可用
        self._has_wechat = self._need_wechat and self._check_wechat_available()
        
        # 设置日志
        self._setup_logging()
        
        # 初始化时尝试使用现有的登录状态
        if self._has_wechat:
            self._try_load_login_status()
    
    def _setup_logging(self):
        """设置日志配置"""
        try:
            # 检查是否已经配置了日志
            if not logging.getLogger().handlers:
                logging.basicConfig(
                    level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s'
                )
            logging.info("Logging setup completed")
        except Exception as e:
            print(f"Warning: Failed to setup logging: {str(e)}")
    
    def _try_load_login_status(self):
        """尝试加载现有的登录状态"""
        try:
            if os.path.exists(self._itchat_pkl):
                itchat.auto_login(hotReload=True, statusStorageDir=self._itchat_pkl)
                if self.check_login_status():
                    self._logged_in = True
                    logging.info("Successfully loaded existing login status")
                    return True
        except Exception as e:
            logging.warning(f"Failed to load existing login status: {str(e)}")
            self.clean_login_cache()
        return False
    
    def clean_login_cache(self):
        """清理登录缓存文件"""
        try:
            if os.path.exists(self._itchat_pkl):
                os.remove(self._itchat_pkl)
                logging.info("Successfully removed WeChat login cache file")
                return True
        except Exception as e:
            logging.error(f"Failed to remove WeChat login cache file: {str(e)}")
        return False
    
    def login(self, max_retries: int = 3, clean_cache: bool = False) -> bool:
        """登录微信，支持重试机制
        
        Args:
            max_retries: 最大重试次数
            clean_cache: 是否清理登录缓存
        """
        with self._login_lock:
            # 如果已经登录且状态有效，直接返回
            if self._logged_in and self.check_login_status():
                return True
            
            # 如果要求清理缓存或者加载现有状态失败，则尝试重新登录
            if clean_cache:
                self.clean_login_cache()
            elif self._try_load_login_status():
                return True
            
            for attempt in range(max_retries):
                try:
                    itchat.auto_login(hotReload=True, 
                                    enableCmdQR=2,
                                    statusStorageDir=self._itchat_pkl,
                                    loginCallback=self._on_login,
                                    exitCallback=self._on_logout)
                    self._logged_in = True
                    logging.info("WeChat logged in successfully")
                    return True
                except KeyboardInterrupt:
                    logging.info("Login cancelled by user")
                    return False
                except Exception as e:
                    logging.error(f"Login attempt {attempt + 1}/{max_retries} failed: {str(e)}")
                    if attempt < max_retries - 1:
                        self.clean_login_cache()
                        time.sleep(3)
            
            self._logged_in = False
            return False
    
    def _on_login(self):
        """登录成功回调"""
        self._logged_in = True
        logging.info("WeChat login callback: Login successful")
    
    def _on_logout(self):
        """登出回调"""
        self._logged_in = False
        logging.info("WeChat logout callback: Logged out")
    
    def check_login_status(self) -> bool:
        """检查当前登录状态"""
        try:
            # 尝试执行一个简单的API调用来验证登录状态
            friends = itchat.search_friends()
            return bool(friends and len(friends) > 0)
        except Exception as e:
            logging.warning(f"Login status check failed: {str(e)}")
            self._logged_in = False
            return False
    
    def ensure_login(self) -> bool:
        """确保登录状态，如果未登录则尝试登录"""
        if not self._need_wechat:
            logging.info("WeChat functionality not needed based on configuration")
            return False
            
        if not self._has_wechat:
            logging.error("WeChat functionality not available")
            return False
            
        if self._logged_in and self.check_login_status():
            return True
        return self.login()
    
    def send_message(self, msg: str, receiver: str) -> bool:
        """发送消息到指定接收者"""
        if not self.ensure_login():
            return False
            
        try:
            # 如果receiver已经是UserID格式（以@开头），直接使用
            if receiver.startswith('@'):
                user_id = receiver
            else:
                user_id = self.get_user_id(receiver)
                
            if not user_id:
                logging.error(f"Cannot find receiver: {receiver}")
                return False
            
            # 验证用户ID是否有效
            if user_id != 'filehelper':  # 文件传输助手不需要验证
                # 尝试更新用户信息
                if user_id.startswith('@'):
                    # 检查是否是群聊
                    if itchat.search_chatrooms(userName=user_id):
                        pass  # 群聊ID有效
                    elif itchat.search_friends(userName=user_id):
                        pass  # 好友ID有效
                    else:
                        logging.error(f"Invalid or expired user ID: {user_id}")
                        return False
            
            # 发送消息
            logging.info(f"Sending message to {user_id}")
            result = itchat.send(msg, toUserName=user_id)
            
            if result['BaseResponse']['Ret'] != 0:
                logging.error(f"Failed to send message, error code: {result['BaseResponse']['Ret']}")
                return False
                
            # 只记录消息的前100个字符，避免日志过长
            preview = msg[:100] + '...' if len(msg) > 100 else msg
            logging.info(f"Message sent successfully to {user_id}: {preview}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to send message: {str(e)}")
            return False
            
    def get_user_id(self, receiver: str) -> Optional[str]:
        """根据备注名或昵称获取用户ID"""
        try:
            # 如果已经是UserID格式，直接返回
            if receiver.startswith('@'):
                return receiver
                
            # 文件传输助手
            if receiver.lower() in ['filehelper', 'file helper']:
                return 'filehelper'
            
            # 通过备注名搜索
            users = itchat.search_friends(remarkName=receiver)
            if users:
                user_id = users[0]['UserName']
                logging.info(f"Found user by remarkName: {receiver} -> {user_id}")
                return user_id
            
            # 通过昵称搜索
            users = itchat.search_friends(nickName=receiver)
            if users:
                user_id = users[0]['UserName']
                logging.info(f"Found user by nickName: {receiver} -> {user_id}")
                return user_id
            
            # 搜索群聊
            groups = itchat.search_chatrooms(name=receiver)
            if groups:
                group_id = groups[0]['UserName']
                logging.info(f"Found group: {receiver} -> {group_id}")
                return group_id
            
            logging.error(f"No matching user or group found for: {receiver}")
            return None
            
        except Exception as e:
            logging.error(f"Error searching for user {receiver}: {str(e)}")
            return None
    
    def logout(self):
        """主动登出微信"""
        if self._logged_in and not self._is_shutting_down:
            try:
                self._is_shutting_down = True
                itchat.logout()
                self._logged_in = False
                self.clean_login_cache()  # 清理登录缓存
                logging.info("WeChat logged out successfully")
            except Exception as e:
                if 'sys.meta_path' not in str(e):  # 忽略Python关闭时的特定错误
                    logging.warning(f"Error during logout: {str(e)}")
            finally:
                self._is_shutting_down = False
    
    def __del__(self):
        """析构函数，不做任何清理"""
        pass
    
    def _check_wechat_available(self) -> bool:
        """检查是否安装了itchat"""
        try:
            import itchat
            return True
        except ImportError:
            logging.warning("WeChat functionality not available: itchat not installed")
            return False
    
# 为了保持向后兼容，保留原有的函数接口
_manager = WeChatManager()

def setup_logging():
    """设置日志"""
    _manager._setup_logging()

def login_wechat():
    """登录微信"""
    return _manager.login()

def is_logged_in():
    """检查是否已登录"""
    return _manager.check_login_status()

def search_contacts(query=None):
    """搜索微信联系人
    
    Args:
        query: 搜索关键词，支持备注名、微信号、昵称等，为空则显示所有联系人
    """
    if not is_logged_in():
        if not login_wechat():
            return
    
    # 获取所有好友
    friends = itchat.get_friends(update=True)
    
    # 准备显示的数据
    contact_data = []
    for friend in friends:
        if query is None or query.lower() in str(friend).lower():
            contact_data.append([
                friend['UserName'],  # 用户ID
                friend['RemarkName'] or '无备注',  # 备注名
                friend['NickName'],  # 昵称
                friend['Signature'][:20] + '...' if friend['Signature'] and len(friend['Signature']) > 20 else friend['Signature'] or '无签名'  # 签名
            ])
    
    # 使用 tabulate 格式化输出
    if contact_data:
        headers = ['UserName', '备注名', '昵称', '签名']
        print("\n" + tabulate(contact_data, headers=headers, tablefmt='grid'))
        print(f"\n共找到 {len(contact_data)} 个联系人")
    else:
        print("未找到匹配的联系人")

def search_groups(query=None):
    """搜索微信群
    
    Args:
        query: 搜索关键词，支持群名称，为空则显示所有群
    """
    if not is_logged_in():
        if not login_wechat():
            return
    
    # 获取所有群
    groups = itchat.get_chatrooms(update=True)
    
    # 准备显示的数据
    group_data = []
    for group in groups:
        if query is None or query.lower() in str(group).lower():
            group_data.append([
                group['UserName'],  # 群ID
                group['NickName'],  # 群名称
                len(group['MemberList']) if 'MemberList' in group else '未知',  # 成员数量
            ])
    
    # 使用 tabulate 格式化输出
    if group_data:
        headers = ['UserName', '群名称', '成员数量']
        print("\n" + tabulate(group_data, headers=headers, tablefmt='grid'))
        print(f"\n共找到 {len(group_data)} 个群")
    else:
        print("未找到匹配的群")

def main():
    """主函数"""
    setup_logging()
    
    while True:
        print("\n=== 微信联系人查询工具 ===")
        print("1. 搜索联系人")
        print("2. 搜索群")
        print("3. 显示所有联系人")
        print("4. 显示所有群")
        print("0. 退出")
        
        choice = input("\n请选择功能 (0-4): ").strip()
        
        if choice == '0':
            break
        elif choice in ['1', '2']:
            query = input("请输入搜索关键词: ").strip()
            if choice == '1':
                search_contacts(query)
            else:
                search_groups(query)
        elif choice == '3':
            search_contacts()
        elif choice == '4':
            search_groups()
        else:
            print("无效的选择，请重试")
    
    print("感谢使用！")

if __name__ == "__main__":
    main() 