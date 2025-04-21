#!/usr/bin/env python3
# coding: utf-8
"""
F5 外部數據組來源動態更新腳本
讀取 Zone 列表和 Landing IP，動態產生更新命令，
區分處理不同 FQDN Zone 的更新邏輯 (例如 phishtw 只有單一 Landing IP)，
並透過 SSH 連接多台 F5 設備執行更新。
密碼從環境變數讀取。
"""

import paramiko
import time
import logging
import os
import sys
import ipaddress
from datetime import datetime

# --- 設定日誌 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("f5_updater.log", encoding='utf-8'), # 指定 UTF-8
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("F5_Updater")

# --- 組態設定 ---
# F5 設備資訊文件 (格式: IP地址,用戶名,設備名稱(可選))
# 密碼將從環境變數 F5_PASSWORD_{IP} 或 F5_PASSWORD_{設備名稱} 讀取 (腳本會將名稱中的 '-' 替換為 '_')
F5_DEVICES_FILE = "f5_devices.txt"

# rpz_converter_v8.py 的相關設定 (需要與 converter 腳本一致)
FQDN_ZONE_LIST_FILE = "rpz_fqdn_zone.txt" # FQDN Zone 列表檔案
IP_ZONE_LIST_FILE = "rpz_ip_zone.txt"     # IP Zone 列表檔案
KNOWN_LANDING_IPS_FILE = "known_landing_ips.txt" # 已知 Landing IP 檔案
DEFAULT_KNOWN_LANDING_IPS = { # 預設 Landing IP (如果檔案不存在)
    "34.102.218.71", "182.173.0.181", "112.121.114.76",
    "210.64.24.25", "210.69.155.3", "35.206.236.238"
}
# *** 定義 phishtw 的特定 Landing IP ***
PHISHTW_ZONE_NAME = "phishtw." # phishtw zone 的名稱 (注意結尾的點)
PHISHTW_LANDING_IP = "182.173.0.170" # phishtw 對應的唯一 Landing IP (請確認此 IP 是否正確)

# 提供 Data Group 檔案的 HTTP 伺服器地址和端口 (需要與 converter 腳本一致)
HTTP_SERVER = "10.8.38.99:8080"

# 更新間隔 (秒)
UPDATE_INTERVAL_SECONDS = 5 * 60 # 5 分鐘

# --- 函數定義 ---

def create_f5_devices_file_example():
    """如果 f5_devices.txt 不存在，創建一個範例文件"""
    if os.path.exists(F5_DEVICES_FILE):
        logger.debug(f"文件 {F5_DEVICES_FILE} 已存在，跳過創建範例。")
        return
    # 更新範例註解，提示環境變數名稱會用底線
    example_content = """# 格式: IP地址,用戶名,設備名稱(可選)
# 密碼將從環境變數讀取 (例如 F5_PASSWORD_F5_Device1 或 F5_PASSWORD_10_8_38_234)
# 注意：腳本會自動將設備名和IP中的 '-' 替換為 '_' 來查找環境變數
# 每行一台設備
#10.8.38.234,admin,F5-Device1
#10.8.34.6,admin,F5-Device2
# 使用 # 可以註釋掉不需要的設備
"""
    try:
        with open(F5_DEVICES_FILE, "w", encoding='utf-8') as f:
            f.write(example_content)
        logger.info(f"已創建 F5 設備資訊範例文件 {F5_DEVICES_FILE}")
        logger.info("請編輯此文件，填入實際的 F5 設備資訊 (IP, 用戶名, 可選的設備名)，"
                    "並確保已設定對應的環境變數密碼(使用底線而非減號)後再運行此腳本。")
    except Exception as e:
        logger.error(f"創建範例文件 {F5_DEVICES_FILE} 時發生錯誤: {e}")

def read_f5_devices():
    """
    讀取 F5 設備資訊文件。
    返回包含設備字典的列表，每個字典包含 'ip', 'username', 'name', 'password'。
    密碼從環境變數 F5_PASSWORD_{name} 或 F5_PASSWORD_{ip} 讀取。
    在查找環境變數時，會將 name 和 ip 中的 '-' 替換為 '_'。
    """
    devices = []
    if not os.path.exists(F5_DEVICES_FILE):
        logger.error(f"F5 設備文件 {F5_DEVICES_FILE} 不存在。")
        create_f5_devices_file_example()
        return []

    try:
        with open(F5_DEVICES_FILE, "r", encoding='utf-8') as f:
            for i, line in enumerate(f):
                line_num = i + 1
                line = line.strip()
                # 跳過空行和註釋
                if not line or line.startswith("#"):
                    continue

                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    logger.warning(f"格式錯誤於 {F5_DEVICES_FILE} 第 {line_num} 行: {line} (至少需要 IP 和用戶名)")
                    continue

                ip = parts[0]
                username = parts[1]
                # 如果提供了設備名，使用它；否則使用 IP 作為名稱
                name = parts[2] if len(parts) > 2 and parts[2] else ip

                # 在查找環境變數前，將名稱和IP中的 '-' 替換為 '_'
                name_for_env_var = name.replace('-', '_')
                ip_for_env_var = ip.replace('-', '_') # IP 通常沒有減號，但也替換以防萬一

                # 從環境變數讀取密碼
                # 優先嘗試 F5_PASSWORD_{處理過的設備名稱}
                password_env_var_name = f"F5_PASSWORD_{name_for_env_var}"
                password = os.environ.get(password_env_var_name)

                if not password:
                    # 如果按名稱找不到，再嘗試 F5_PASSWORD_{處理過的IP地址}
                    password_env_var_ip = f"F5_PASSWORD_{ip_for_env_var}"
                    password = os.environ.get(password_env_var_ip)
                    if password:
                         logger.debug(f"為設備 {name} ({ip}) 找到環境變數密碼: {password_env_var_ip}")
                    else:
                         # 錯誤訊息顯示實際查找的變數名 (用底線)
                         logger.error(f"找不到設備 {name} ({ip}) 的密碼環境變數。"
                                      f"請設定 {password_env_var_name} 或 {password_env_var_ip}。")
                         continue # 跳過此設備
                else:
                     logger.debug(f"為設備 {name} ({ip}) 找到環境變數密碼: {password_env_var_name}")


                device = {
                    "ip": ip,
                    "username": username,
                    "password": password,
                    "name": name # 儲存原始名稱用於日誌等
                }
                devices.append(device)
    except Exception as e:
        logger.error(f"讀取 F5 設備文件 {F5_DEVICES_FILE} 時出錯: {e}", exc_info=True)

    return devices

def read_file_lines(file_path):
    """讀取文件行列表，忽略空行和註釋"""
    lines = []
    if not os.path.exists(file_path):
        logger.warning(f"列表文件不存在: {file_path}")
        return lines
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        logger.debug(f"從 {file_path} 讀取了 {len(lines)} 行。")
    except Exception as e:
        logger.error(f"讀取列表文件 {file_path} 時發生錯誤: {e}")
    return lines

def load_landing_ips():
    """載入 Landing IP 清單"""
    ips = set()
    if os.path.exists(KNOWN_LANDING_IPS_FILE):
        try:
            with open(KNOWN_LANDING_IPS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    ip = line.strip()
                    if ip and not ip.startswith('#'):
                        try:
                            ipaddress.ip_address(ip) # 驗證 IP
                            ips.add(ip)
                        except ValueError:
                            logger.warning(f"在 {KNOWN_LANDING_IPS_FILE} 中發現無效 IP: {ip}")
            logger.info(f"從 {KNOWN_LANDING_IPS_FILE} 載入了 {len(ips)} 個 Landing IP。")
        except Exception as e:
            logger.error(f"讀取 {KNOWN_LANDING_IPS_FILE} 時發生錯誤: {e}")
            logger.info("將使用預設 Landing IP 清單。")
            ips = DEFAULT_KNOWN_LANDING_IPS.copy()
    else:
        logger.info(f"未找到 {KNOWN_LANDING_IPS_FILE}，將使用預設 Landing IP 清單。")
        ips = DEFAULT_KNOWN_LANDING_IPS.copy()
    # *** 將 phishtw 的特定 IP 也加入到 landing_ips 集合中，確保它被考慮 ***
    # *** 但在 generate_update_commands 中會區分處理 ***
    if PHISHTW_LANDING_IP:
        try:
            ipaddress.ip_address(PHISHTW_LANDING_IP)
            ips.add(PHISHTW_LANDING_IP)
        except ValueError:
             logger.error(f"設定的 PHISHTW_LANDING_IP ({PHISHTW_LANDING_IP}) 不是有效的 IP 地址。")
    return ips

def generate_update_commands():
    """
    根據 Zone 列表和 Landing IP 動態產生需要執行的 TMSH 命令列表。
    特殊處理 phishtw zone，只使用其指定的單一 Landing IP。
    其他 FQDN zone 則排除 phishtw 的 IP。
    """
    commands = []
    landing_ips = load_landing_ips() # 包含所有已知 IP + phishtw 的特定 IP
    fqdn_zones = read_file_lines(FQDN_ZONE_LIST_FILE)
    ip_zones = read_file_lines(IP_ZONE_LIST_FILE)

    # 1. 處理 FQDN Zone 相關的 Data Group
    for zone in fqdn_zones:
        zone_prefix = zone.rstrip('.').replace('.', '_') # e.g., rpztw, phishtw

        # 區分處理 phishtw 和其他 FQDN zone
        if zone == PHISHTW_ZONE_NAME:
            # --- 處理 phishtw (單一 IP) ---
            if not PHISHTW_LANDING_IP:
                 logger.warning(f"已設定處理 {PHISHTW_ZONE_NAME} 但未設定有效的 PHISHTW_LANDING_IP，跳過此 zone 的更新命令生成。")
                 continue

            ip = PHISHTW_LANDING_IP
            ip_filename_part = ip.replace('.', '_')
            dg_name_base = f"{zone_prefix}_{ip_filename_part}" # e.g., phishtw_182_173_0_170
            dg_name = dg_name_base.replace('-', '_') # Data group 名稱用底線
            file_url = f"http://{HTTP_SERVER}/{dg_name_base}.txt" # URL 中的檔名保持原始生成邏輯
            commands.append(f"modify ltm data-group external {dg_name} source-path {file_url}")
            logger.info(f"為 {zone} 產生單一更新命令: {dg_name} -> {file_url}")

        else:
            # --- 處理其他 FQDN zones (例如 rpztw，使用 landing IPs 但排除 phishtw 的 IP) ---
            logger.info(f"為 {zone} 根據 Landing IPs (排除 {PHISHTW_LANDING_IP} 若存在) 產生更新命令...")
            count = 0
            for ip in landing_ips:
                # *** 修改：如果當前 IP 是 phishtw 的專用 IP，則跳過，不為 rpztw 等 zone 產生此 IP 的指令 ***
                if ip == PHISHTW_LANDING_IP:
                    logger.debug(f"跳過為 {zone} 產生 IP {ip} 的命令，因為它是為 {PHISHTW_ZONE_NAME} 保留的。")
                    continue

                ip_filename_part = ip.replace('.', '_') # e.g., 34_102_218_71
                dg_name_base = f"{zone_prefix}_{ip_filename_part}" # e.g., rpztw_34_102_218_71
                dg_name = dg_name_base.replace('-', '_') # Data group 名稱用底線
                file_url = f"http://{HTTP_SERVER}/{dg_name_base}.txt" # URL 中的檔名保持原始生成邏輯
                commands.append(f"modify ltm data-group external {dg_name} source-path {file_url}")
                count += 1
            logger.info(f"為 {zone} 產生了 {count} 條更新命令。")


    # 2. 處理 IP Zone 相關的 Data Group
    for zone in ip_zones:
        zone_prefix = zone.rstrip('.').replace('.', '_') # e.g., some_ip_zone
        # 假設 F5 上的 Data Group 名稱對應 converter 產生的檔名
        dg_name_base = f"{zone_prefix}_ip"
        dg_name = dg_name_base.replace('-', '_') # Data group 名稱用底線
        file_url = f"http://{HTTP_SERVER}/{dg_name_base}.txt" # URL 中的檔名保持原始生成邏輯
        commands.append(f"modify ltm data-group external {dg_name} source-path {file_url}")

    # 3. 處理合併的 IP 黑名單 Data Group (假設 F5 上有這個 Data Group)
    # 這個 Data Group 的名稱需要確認 F5 上的實際設定
    merged_ip_dg_name_base = "rpzip_blacklist" # 假設名稱
    merged_ip_dg_name = merged_ip_dg_name_base.replace('-', '_') # Data group 名稱用底線
    merged_ip_file_url = f"http://{HTTP_SERVER}/rpzip_blacklist.txt"
    commands.append(f"modify ltm data-group external {merged_ip_dg_name} source-path {merged_ip_file_url}")

    # 4. 可選：處理合併的 FQDN Key/Value Data Group (如果 F5 上有用到)
    # merged_fqdn_kv_dg_name_base = "rpz_blacklist_fqdn_kv" # 假設名稱
    # merged_fqdn_kv_dg_name = merged_fqdn_kv_dg_name_base.replace('-', '_')
    # merged_fqdn_kv_file_url = f"http://{HTTP_SERVER}/rpz_blacklist_fqdn_kv.txt"
    # commands.append(f"modify ltm data-group external {merged_fqdn_kv_dg_name} source-path {merged_fqdn_kv_file_url}")

    logger.info(f"根據設定共產生了 {len(commands)} 條更新命令。")
    if commands:
         logger.debug("產生的命令預覽 (前 5 條):")
         for cmd in commands[:5]:
              logger.debug(f"- {cmd}")
    return commands

def execute_commands_on_device(device, commands):
    """SSH 到單台 F5 設備並執行命令列表"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    success = True

    try:
        logger.info(f"正在連接到 {device['name']} ({device['ip']})...")
        ssh.connect(
            hostname=device['ip'],
            username=device['username'],
            password=device['password'], # 從環境變數讀取的密碼
            timeout=20 # 增加超時
        )
        logger.info(f"成功連接到 {device['name']}。")

        # 進入 TMSH 並執行命令
        for command in commands:
            full_command = f"tmsh {command}"
            logger.info(f"在 {device['name']} 上執行: {command}") # 日誌中省略 tmsh 前綴

            stdin, stdout, stderr = ssh.exec_command(full_command, timeout=60) # 增加命令超時
            exit_status = stdout.channel.recv_exit_status() # 獲取命令退出狀態碼
            output = stdout.read().decode('utf-8', errors='ignore')
            error = stderr.read().decode('utf-8', errors='ignore')

            if exit_status == 0:
                logger.info(f"命令成功 on {device['name']}: {output.strip() if output.strip() else '無輸出'}")
            else:
                # 檢查是否為 "Data group ... does not exist" 錯誤
                if "01020036:3" in error or "was not found" in error:
                     logger.warning(f"命令跳過 on {device['name']} (Data Group 不存在): {command}")
                     # 可以選擇不將此視為完全失敗
                # 檢查是否為 404 錯誤
                elif "error: 404" in error or "exit_code (22)" in error:
                     logger.warning(f"命令跳過 on {device['name']} (源文件 404 Not Found): {command}")
                     # 可以選擇不將此視為完全失敗
                else:
                     logger.error(f"命令失敗 on {device['name']} (Exit Status: {exit_status}): {command}")
                     if error:
                          logger.error(f"錯誤詳情: {error.strip()}")
                     else:
                          logger.error("無標準錯誤輸出。")
                     success = False # 標記此設備更新失敗

        ssh.close()
        logger.info(f"已從 {device['name']} 斷開連接。")

    except paramiko.AuthenticationException:
        logger.error(f"連接 {device['name']} ({device['ip']}) 身份驗證失敗！請檢查用戶名或對應的環境變數密碼。")
        success = False
    except paramiko.SSHException as sshException:
        logger.error(f"無法建立 SSH 連接到 {device['name']} ({device['ip']}): {sshException}")
        success = False
    except Exception as e:
        logger.error(f"連接或執行命令時發生未知錯誤 ({device['name']}, {device['ip']}): {e}", exc_info=True)
        success = False
    finally:
        try:
            ssh.close()
        except:
            pass
    return success

def update_all_devices():
    """讀取設備列表，產生命令，並更新所有 F5 設備"""
    logger.info("開始更新所有 F5 設備的外部數據組來源...")

    devices = read_f5_devices()
    if not devices:
        logger.warning("在 f5_devices.txt 中未找到有效的設備資訊或無法讀取密碼。")
        return

    commands_to_run = generate_update_commands()
    if not commands_to_run:
        logger.warning("未能產生任何更新命令，請檢查 Zone 列表和 Landing IP 設定。")
        return

    logger.info(f"將對 {len(devices)} 台 F5 設備執行 {len(commands_to_run)} 條更新命令。")

    success_count = 0
    failure_devices = []
    for device in devices:
        if execute_commands_on_device(device, commands_to_run):
            success_count += 1
        else:
            failure_devices.append(device['name'])

    logger.info(f"更新週期完成: {success_count}/{len(devices)} 台設備更新成功。")
    if failure_devices:
        logger.warning(f"更新失敗的設備: {', '.join(failure_devices)}")

def main():
    """主函數"""
    logger.info("F5 外部數據組動態更新腳本已啟動")

    # 檢查 f5_devices.txt 是否存在，如果不存在則創建範例
    create_f5_devices_file_example()
    # 提醒用戶檢查設定
    logger.info(f"請確保 {F5_DEVICES_FILE} 文件已配置，")
    logger.info(f"且已為每個設備設定了對應的 F5_PASSWORD_{{name}} 或 F5_PASSWORD_{{ip}} 環境變數 (名稱中的 '-' 會被替換為 '_')。") # 更新提示
    logger.info(f"同時確認 {FQDN_ZONE_LIST_FILE}, {IP_ZONE_LIST_FILE}, {KNOWN_LANDING_IPS_FILE} (或預設值) 以及 HTTP_SERVER ({HTTP_SERVER}) 設定正確。")
    logger.info(f"並確認 PHISHTW_ZONE_NAME ('{PHISHTW_ZONE_NAME}') 和 PHISHTW_LANDING_IP ('{PHISHTW_LANDING_IP}') 設定正確。") # 新增提示
    logger.info("-" * 30)


    try:
        # 立即執行一次
        update_all_devices()

        # 進入定時循環
        logger.info(f"腳本將每 {UPDATE_INTERVAL_SECONDS} 秒執行一次更新，按 Ctrl+C 可停止。")

        while True:
            next_run = time.time() + UPDATE_INTERVAL_SECONDS
            next_run_time_dt = datetime.fromtimestamp(next_run)
            # 顯示絕對時間和相對時間
            logger.info(f"下次執行時間: {next_run_time_dt.strftime('%Y-%m-%d %H:%M:%S')} (約 {(UPDATE_INTERVAL_SECONDS / 60):.0f} 分鐘後)")

            time.sleep(UPDATE_INTERVAL_SECONDS)
            update_all_devices()

    except KeyboardInterrupt:
        logger.info("接收到中斷信號，腳本已停止。")
    except Exception as e:
        logger.error(f"主循環執行過程中發生未預期的錯誤: {e}", exc_info=True)

if __name__ == "__main__":
    main()

