#!/usr/bin/env python3
"""
F5 數據組自動更新腳本
每5分鐘登入多台F5設備並更新外部數據組的來源路徑
"""

import paramiko
import time
import logging
import os
from datetime import datetime
import sys

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("f5_updater.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# F5設備資訊文件
F5_DEVICES_FILE = "f5_devices.txt"
# HTTP服務器地址
HTTP_SERVER = "10.8.34.99:8080"

# 固定的命令列表，不需要讀取zone列表文件
COMMANDS = [
    f"modify ltm data-group external rpz_rpztw source-path http://{HTTP_SERVER}/rpztw_blacklist.txt",
    f"modify ltm data-group external rpz_phishingtw source-path http://{HTTP_SERVER}/phishingtw_blacklist.txt",
    f"modify ltm data-group external rpz_ip source-path http://{HTTP_SERVER}/rpzip_blacklist.txt"
]

def create_f5_devices_file():
    """創建一個包含F5設備資訊的範例文件"""
    if os.path.exists(F5_DEVICES_FILE):
        logger.info(f"文件 {F5_DEVICES_FILE} 已存在，跳過創建")
        return
    
    example_content = """# 格式: IP地址,用戶名,密碼,設備名稱(可選)
# 每行一台設備
10.1.1.1,admin,adminPassword,F5-Device1
10.1.1.2,admin,adminPassword,F5-Device2
# 10.1.1.3,admin,adminPassword,F5-Device3  # 使用 # 可以註釋掉不需要的設備
"""
    with open(F5_DEVICES_FILE, "w") as f:
        f.write(example_content)
    
    logger.info(f"已創建F5設備資訊文件 {F5_DEVICES_FILE}")
    logger.info("請編輯此文件，填入實際的F5設備資訊後再運行此腳本")

def read_f5_devices():
    """讀取F5設備資訊文件"""
    devices = []
    
    try:
        with open(F5_DEVICES_FILE, "r") as f:
            for line in f:
                line = line.strip()
                # 跳過空行和註釋
                if not line or line.startswith("#"):
                    continue
                
                parts = line.split(",")
                if len(parts) >= 3:
                    device = {
                        "ip": parts[0],
                        "username": parts[1],
                        "password": parts[2],
                        "name": parts[3] if len(parts) > 3 else parts[0]
                    }
                    devices.append(device)
    except Exception as e:
        logger.error(f"讀取F5設備文件時出錯: {e}")
    
    return devices

def execute_commands(device, commands):
    """SSH到F5設備並執行命令"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        logger.info(f"正在連接到 {device['name']} ({device['ip']})")
        ssh.connect(
            hostname=device['ip'],
            username=device['username'],
            password=device['password'],
            timeout=10
        )
        
        # 運行TMSH命令需要先進入TMSH環境
        for command in commands:
            full_command = f"tmsh {command}"
            logger.info(f"執行命令: {full_command}")
            
            stdin, stdout, stderr = ssh.exec_command(full_command)
            output = stdout.read().decode()
            error = stderr.read().decode()
            
            if error:
                logger.error(f"命令執行錯誤: {error}")
            else:
                logger.info(f"命令執行成功: {output if output else '無輸出'}")
        
        ssh.close()
        logger.info(f"已從 {device['name']} 斷開連接")
        return True
    
    except Exception as e:
        logger.error(f"連接或執行命令時出錯 ({device['ip']}): {e}")
        try:
            ssh.close()
        except:
            pass
        return False

def update_all_devices():
    """更新所有F5設備"""
    logger.info("開始更新所有F5設備的數據組")
    
    devices = read_f5_devices()
    if not devices:
        logger.error(f"找不到任何F5設備資訊，請確認 {F5_DEVICES_FILE} 文件格式正確")
        return
    
    logger.info(f"找到 {len(devices)} 台F5設備")
    
    success_count = 0
    for device in devices:
        if execute_commands(device, COMMANDS):
            success_count += 1
    
    logger.info(f"更新完成: {success_count}/{len(devices)} 台設備更新成功")

def main():
    """主函數"""
    logger.info("F5 數據組自動更新腳本已啟動")
    
    # 檢查必要的文件是否存在
    if not os.path.exists(F5_DEVICES_FILE):
        create_f5_devices_file()
        logger.info("請編輯 f5_devices.txt 文件，填入實際的F5設備資訊後再運行此腳本")
        return
    
    try:
        # 立即執行一次
        update_all_devices()
        
        # 每5分鐘執行一次
        logger.info("腳本將每5分鐘執行一次，按 Ctrl+C 可停止")
        
        while True:
            next_run = time.time() + 300  # 5分鐘 = 300秒
            next_run_time = datetime.fromtimestamp(next_run).strftime('%H:%M:%S')
            logger.info(f"下次執行時間: {next_run_time}")
            
            time.sleep(300)
            update_all_devices()
            
    except KeyboardInterrupt:
        logger.info("腳本已手動停止")
    except Exception as e:
        logger.error(f"執行過程中發生錯誤: {e}")

if __name__ == "__main__":
    main()
