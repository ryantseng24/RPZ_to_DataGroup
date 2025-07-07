#!/usr/bin/env python3
# coding: utf-8
"""
F5 外部數據組與 iRule 動態更新腳本
1. 讀取 Zone 列表和 Landing IP，動態產生 Data Group 管理列表。
2. 檢查 F5 上的 Data Group 是否存在，不存在則建立 (直接指定 source-path)，然後更新 source-path。
3. 讀取本地 iRule 範本，填入最新的 Data Group 映射，並將完整 iRule 推送到 F5 (透過 iControl REST API)。
密碼從環境變數讀取。
"""

import paramiko
import time
import logging
import os
import sys
import ipaddress
import re # 用於 iRule 修改
from datetime import datetime
import requests
import json

# --- 設定日誌 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("f5_updater.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("F5_Updater_Auto")

# --- 組態設定 ---
F5_DEVICES_FILE = "f5_devices.txt"
FQDN_ZONE_LIST_FILE = "rpz_fqdn_zone.txt"
IP_ZONE_LIST_FILE = "rpz_ip_zone.txt"
KNOWN_LANDING_IPS_FILE = "known_landing_ips.txt"
DEFAULT_KNOWN_LANDING_IPS = {
    "34.102.218.71", "182.173.0.181", "112.121.114.76",
    "210.64.24.25", "210.69.155.3", "35.206.236.238"
}
PHISHTW_ZONE_NAME = "phishtw."
PHISHTW_LANDING_IP = "182.173.0.170"
HTTP_SERVER = "10.8.38.223:8080" # 腳本執行機的 IP，用於 F5 下載
UPDATE_INTERVAL_SECONDS = 5 * 60
LOCAL_MASTER_IRULE_FILE = "/opt/rpz_project/dns_rpz_irule_template.tcl"
TARGET_IRULE_NAME_ON_F5 = "rpz_fqdn_v10"
IRULE_DG_MAP_START_MARKER = "# START_DG_IP_MAP_BLOCK"
IRULE_DG_MAP_END_MARKER = "# END_DG_IP_MAP_BLOCK"
ENABLE_IRULE_AUTO_UPDATE = True
MANAGE_RPZIP_BLACKLIST = False

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    logger.warning("urllib3 未安裝，無法禁用 InsecureRequestWarning。")
F5_API_VERIFY_SSL = False

# --- 函數定義 ---

def create_f5_devices_file_example():
    if os.path.exists(F5_DEVICES_FILE):
        logger.debug(f"文件 {F5_DEVICES_FILE} 已存在。")
        return
    example_content = """# 格式: IP地址,用戶名,設備名稱(可選)
#10.8.38.234,admin,F5_Device1
"""
    try:
        with open(F5_DEVICES_FILE, "w", encoding='utf-8') as f:
            f.write(example_content)
        logger.info(f"已創建範例文件 {F5_DEVICES_FILE}，請填入實際資訊並設定環境變數密碼。")
    except Exception as e:
        logger.error(f"創建範例文件 {F5_DEVICES_FILE} 時出錯: {e}")

def read_f5_devices():
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
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    logger.warning(f"格式錯誤於 {F5_DEVICES_FILE} 第 {line_num} 行: {line}")
                    continue
                ip, username = parts[0], parts[1]
                name = parts[2] if len(parts) > 2 and parts[2] else ip
                name_for_env_var = name.replace('-', '_')
                ip_for_env_var = ip.replace('-', '_')
                password_env_var_name = f"F5_PASSWORD_{name_for_env_var}"
                password = os.environ.get(password_env_var_name)
                if not password:
                    password_env_var_ip = f"F5_PASSWORD_{ip_for_env_var}"
                    password = os.environ.get(password_env_var_ip)
                    if not password:
                        logger.error(f"找不到設備 {name} ({ip}) 的密碼環境變數。請設定 {password_env_var_name} 或 {password_env_var_ip}。")
                        continue
                devices.append({"ip": ip, "username": username, "password": password, "name": name})
    except Exception as e:
        logger.error(f"讀取 F5 設備文件 {F5_DEVICES_FILE} 時出錯: {e}", exc_info=True)
    return devices

def read_file_lines(file_path):
    lines = []
    if not os.path.exists(file_path):
        logger.warning(f"列表文件不存在: {file_path}")
        return lines
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    except Exception as e:
        logger.error(f"讀取列表文件 {file_path} 時發生錯誤: {e}")
    return lines

def load_landing_ips():
    ips = set()
    if os.path.exists(KNOWN_LANDING_IPS_FILE):
        try:
            with open(KNOWN_LANDING_IPS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    ip_addr = line.strip()
                    if ip_addr and not ip_addr.startswith('#'):
                        try:
                            ipaddress.ip_address(ip_addr)
                            ips.add(ip_addr)
                        except ValueError:
                            logger.warning(f"在 {KNOWN_LANDING_IPS_FILE} 中發現無效 IP: {ip_addr}")
            logger.info(f"從 {KNOWN_LANDING_IPS_FILE} 載入了 {len(ips)} 個 Landing IP。")
        except Exception as e:
            logger.error(f"讀取 {KNOWN_LANDING_IPS_FILE} 時發生錯誤: {e}")
            ips = DEFAULT_KNOWN_LANDING_IPS.copy()
            logger.info(f"將使用預設 Landing IP 清單 ({len(ips)} 個)。")
    else:
        ips = DEFAULT_KNOWN_LANDING_IPS.copy()
        logger.info(f"未找到 {KNOWN_LANDING_IPS_FILE}，將使用預設 Landing IP 清單 ({len(ips)} 個)。")
    if PHISHTW_LANDING_IP:
        try:
            ipaddress.ip_address(PHISHTW_LANDING_IP)
            ips.add(PHISHTW_LANDING_IP)
        except ValueError:
             logger.error(f"設定的 PHISHTW_LANDING_IP ({PHISHTW_LANDING_IP}) 不是有效的 IP 地址。")
    return ips

def generate_datagroup_management_list():
    datagroups_to_manage = []
    irule_map_entries = []
    landing_ips = load_landing_ips()
    fqdn_zones = read_file_lines(FQDN_ZONE_LIST_FILE)
    ip_zones = read_file_lines(IP_ZONE_LIST_FILE)

    for zone in fqdn_zones:
        zone_prefix = zone.rstrip('.').replace('.', '_')
        if zone == PHISHTW_ZONE_NAME:
            if PHISHTW_LANDING_IP:
                ip = PHISHTW_LANDING_IP
                ip_filename_part = ip.replace('.', '_')
                dg_name_base = f"{zone_prefix}_{ip_filename_part}"
                dg_name_on_f5 = dg_name_base.replace('-', '_')
                file_url = f"http://{HTTP_SERVER}/{dg_name_base}.txt"
                datagroups_to_manage.append({'name': dg_name_on_f5, 'file_url': file_url, 'type': 'string'})
                irule_map_entries.append((dg_name_on_f5, ip))
        else:
            for ip in landing_ips:
                if ip == PHISHTW_LANDING_IP and zone != PHISHTW_ZONE_NAME:
                    continue
                ip_filename_part = ip.replace('.', '_')
                dg_name_base = f"{zone_prefix}_{ip_filename_part}"
                dg_name_on_f5 = dg_name_base.replace('-', '_')
                file_url = f"http://{HTTP_SERVER}/{dg_name_base}.txt"
                datagroups_to_manage.append({'name': dg_name_on_f5, 'file_url': file_url, 'type': 'string'})
                irule_map_entries.append((dg_name_on_f5, ip))

    for zone in ip_zones:
        zone_prefix = zone.rstrip('.').replace('.', '_')
        dg_name_base = f"{zone_prefix}_ip"
        dg_name_on_f5 = dg_name_base.replace('-', '_')
        file_url = f"http://{HTTP_SERVER}/{dg_name_base}.txt"
        datagroups_to_manage.append({'name': dg_name_on_f5, 'file_url': file_url, 'type': 'ip'})

    if MANAGE_RPZIP_BLACKLIST:
        merged_ip_dg_name_base = "rpzip_blacklist"
        merged_ip_dg_name_on_f5 = merged_ip_dg_name_base.replace('-', '_')
        merged_ip_file_url = f"http://{HTTP_SERVER}/{merged_ip_dg_name_base}.txt"
        datagroups_to_manage.append({'name': merged_ip_dg_name_on_f5, 'file_url': merged_ip_file_url, 'type': 'ip'})
    else:
        logger.info("MANAGE_RPZIP_BLACKLIST 設定為 False，將不處理 rpzip_blacklist Data Group。")

    logger.info(f"共產生 {len(datagroups_to_manage)} 個 Data Group 需要管理。")
    logger.info(f"iRule 的 dg_ip_map 將包含 {len(irule_map_entries)} 個條目。")
    return datagroups_to_manage, irule_map_entries

def execute_tmsh_command(ssh_client, command, device_name):
    full_command = f"tmsh {command}"
    logger.info(f"在 {device_name} 上執行 (TMSH): {command}")
    try:
        stdin, stdout, stderr = ssh_client.exec_command(full_command, timeout=60)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8', errors='ignore').strip()
        error = stderr.read().decode('utf-8', errors='ignore').strip()

        if exit_status == 0:
            logger.info(f"TMSH 命令成功 on {device_name}: {output if output else '無輸出'}")
            return True, output, ""
        else:
            logger.error(f"TMSH 命令失敗 on {device_name} (Exit Status: {exit_status}): {command}")
            if error: logger.error(f"TMSH 錯誤詳情: {error}")
            return False, output, error
    except Exception as e:
        logger.error(f"執行 TMSH 命令 '{command}' on {device_name} 時發生異常: {e}", exc_info=True)
        return False, "", str(e)

def ensure_datagroup_exists_on_f5(ssh_client, device_name, dg_name, dg_type, file_url):
    """檢查 DG 是否存在，不存在則建立 (直接指定 source-path)，然後更新 source-path"""
    logger.info(f"在 {device_name} 上確保 Data Group '{dg_name}' (類型: {dg_type}) 存在並更新來源 (透過 TMSH)...")
    
    exists_command = f"list ltm data-group external /Common/{dg_name}"
    success_list, output_list, error_list = execute_tmsh_command(ssh_client, exists_command, device_name)

    dg_exists = False
    if success_list and output_list: 
        dg_exists = True
        logger.info(f"Data Group '/Common/{dg_name}' 已存在於 {device_name}。")
    elif "was not found" in error_list or "01020036:3" in error_list : 
        logger.info(f"Data Group '/Common/{dg_name}' 不存在於 {device_name}，將嘗試建立。")
        dg_exists = False
    elif not success_list: 
        logger.error(f"檢查 Data Group '/Common/{dg_name}' 狀態時出錯 on {device_name}。錯誤: {error_list}")
        return False

    if not dg_exists:
        create_command = f"create ltm data-group external /Common/{dg_name} type {dg_type} source-path {file_url}"
        success_create, _, error_create = execute_tmsh_command(ssh_client, create_command, device_name)
        if not success_create:
            logger.warning(f"使用 source-path 建立 Data Group '/Common/{dg_name}' 失敗 on {device_name}。錯誤: {error_create}")
            logger.info(f"嘗試回退：先建立空的 Data Group '/Common/{dg_name}'...")
            dummy_external_file_name = f"{dg_name}"
            create_empty_command = f"create ltm data-group external /Common/{dg_name} type {dg_type} external-file-name {dummy_external_file_name}"
            success_create_empty, _, error_create_empty = execute_tmsh_command(ssh_client, create_empty_command, device_name)
            if not success_create_empty:
                logger.error(f"建立空的 Data Group '/Common/{dg_name}' (類型: {dg_type}) 也失敗 on {device_name}。錯誤: {error_create_empty}")
                return False
            logger.info(f"空的 Data Group '/Common/{dg_name}' (類型: {dg_type}) 成功建立於 {device_name}。現在將更新 source-path。")
            dg_exists = True 
        else:
            logger.info(f"Data Group '/Common/{dg_name}' (類型: {dg_type}) 已使用 source-path 成功建立於 {device_name}。")
            return True 

    if dg_exists: 
        modify_command = f"modify ltm data-group external /Common/{dg_name} source-path {file_url}"
        success_modify, _, error_modify = execute_tmsh_command(ssh_client, modify_command, device_name)
        if not success_modify:
            logger.error(f"更新 Data Group '/Common/{dg_name}' 的 source-path 失敗 on {device_name}。錯誤: {error_modify}")
            return False
        logger.info(f"Data Group '/Common/{dg_name}' 的 source-path 已更新為 {file_url} on {device_name}。")
        return True
    
    return False

def generate_irule_dg_map_tcl_block(irule_map_entries):
    if not irule_map_entries:
        return "set dg_ip_map {}"
    map_lines = []
    for dg_name, ip_address in sorted(list(set(irule_map_entries))):
        map_lines.append(f'    "{dg_name}" "{ip_address}"')
    return "set dg_ip_map {\n" + "\n".join(map_lines) + "\n}"

def update_irule_on_f5_api(device_ip, device_username, device_password, device_name, irule_name_on_f5, local_template_path, irule_map_entries_list):
    if not ENABLE_IRULE_AUTO_UPDATE:
        logger.info(f"iRule 自動更新功能已停用，跳過更新 {irule_name_on_f5} on {device_name}。")
        return True
    logger.info(f"準備透過 API 更新 iRule '{irule_name_on_f5}' on {device_name} ({device_ip})...")
    if not os.path.exists(local_template_path):
        logger.error(f"本地 iRule 範本檔案不存在: {local_template_path}")
        return False
    try:
        with open(local_template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
    except Exception as e:
        logger.error(f"讀取本地 iRule 範本 {local_template_path} 時出錯: {e}")
        return False

    new_dg_map_tcl_block = generate_irule_dg_map_tcl_block(irule_map_entries_list)
    start_marker_escaped = re.escape(IRULE_DG_MAP_START_MARKER)
    end_marker_escaped = re.escape(IRULE_DG_MAP_END_MARKER)
    pattern = re.compile(f"({start_marker_escaped}\\s*\\n)(.*?)(\\n\\s*{end_marker_escaped})", re.DOTALL | re.MULTILINE)
    replacement_string = f"\\1{new_dg_map_tcl_block}\\3"
    modified_irule_content, num_replacements = pattern.subn(replacement_string, template_content)

    if num_replacements == 0:
        logger.error(f"在 iRule 範本 {local_template_path} 中未找到標記 "
                     f"'{IRULE_DG_MAP_START_MARKER}' 和 '{IRULE_DG_MAP_END_MARKER}'，"
                     f"或格式不符。無法自動更新 dg_ip_map。")
        return False
    
    logger.info(f"已根據最新的 Data Group 列表產生 iRule '{irule_name_on_f5}' 的新內容。")
    irule_path_segment = f"~Common~{irule_name_on_f5}"
    api_url = f"https://{device_ip}/mgmt/tm/ltm/rule/{irule_path_segment}"
    headers = {"Content-Type": "application/json"}
    payload = {"apiAnonymous": modified_irule_content.strip()}
    logger.info(f"準備透過 API PATCH 請求更新 iRule '{irule_path_segment}' on {device_name} (內容已省略)。 URL: {api_url}")

    try:
        response = requests.patch(
            api_url,
            auth=(device_username, device_password),
            headers=headers,
            data=json.dumps(payload),
            verify=F5_API_VERIFY_SSL
        )
        response.raise_for_status()
        logger.info(f"iRule '{irule_path_segment}' 透過 API 成功更新於 {device_name}。")
        return True
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"透過 API 更新 iRule '{irule_path_segment}' 時發生 HTTP 錯誤: {http_err}")
        if http_err.response is not None:
            logger.error(f"API 回應狀態碼: {http_err.response.status_code}")
            logger.error(f"API 回應內容: {http_err.response.text}")
        return False
    except requests.exceptions.RequestException as req_err:
        logger.error(f"透過 API 更新 iRule '{irule_path_segment}' 時發生請求錯誤: {req_err}")
        return False
    except Exception as e:
        logger.error(f"透過 API 更新 iRule '{irule_path_segment}' 時發生未知錯誤: {e}", exc_info=True)
        return False

def update_all_devices():
    logger.info("開始 F5 Data Group 與 iRule 更新週期...")
    devices = read_f5_devices()
    if not devices:
        logger.warning("未找到有效的 F5 設備資訊或無法讀取密碼。")
        return

    datagroups_to_manage, irule_map_entries = generate_datagroup_management_list()
    overall_success_count = 0
    failed_devices_summary = {}

    for device in devices:
        device_all_ops_success = True 
        ssh_client_for_dg = None
        try:
            if datagroups_to_manage:
                logger.info(f"準備透過 SSH 連接 {device['name']} ({device['ip']}) 進行 Data Group 操作...")
                ssh_client_for_dg = paramiko.SSHClient()
                ssh_client_for_dg.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh_client_for_dg.connect(
                    hostname=device['ip'], username=device['username'],
                    password=device['password'], timeout=20
                )
                logger.info(f"成功連接到 {device['name']} (SSH for Data Groups)。")
                logger.info(f"開始處理 {len(datagroups_to_manage)} 個 Data Group for {device['name']}...")
                for dg_info in datagroups_to_manage:
                    if not ensure_datagroup_exists_on_f5(ssh_client_for_dg, device['name'], dg_info['name'], dg_info['type'], dg_info['file_url']):
                        device_all_ops_success = False
                if ssh_client_for_dg:
                    ssh_client_for_dg.close()
                    logger.info(f"已從 {device['name']} 斷開 SSH 連接 (Data Groups)。")
            else:
                logger.info(f"沒有 Data Group 需要管理 for {device['name']}。")

            if ENABLE_IRULE_AUTO_UPDATE:
                if device_all_ops_success: 
                    if not update_irule_on_f5_api(
                        device['ip'], device['username'], device['password'], device['name'],
                        TARGET_IRULE_NAME_ON_F5, LOCAL_MASTER_IRULE_FILE, irule_map_entries):
                        device_all_ops_success = False
                else:
                    logger.warning(f"由於 Data Group 更新時發生錯誤，跳過在 {device['name']} 上更新 iRule。")
            else:
                 logger.info(f"iRule 自動更新已停用，跳過在 {device['name']} 上更新 iRule。")

            if device_all_ops_success:
                overall_success_count += 1
            else:
                failed_devices_summary[device['name']] = "部分或全部操作失敗"
        except paramiko.AuthenticationException:
            logger.error(f"連接 {device['name']} ({device['ip']}) 進行 Data Group 操作時身份驗證失敗！")
            failed_devices_summary[device['name']] = "Data Group 操作 - 身份驗證失敗"
        except paramiko.SSHException as sshEx:
            logger.error(f"無法建立 SSH 連接到 {device['name']} ({device['ip']}) 進行 Data Group 操作: {sshEx}")
            failed_devices_summary[device['name']] = "Data Group 操作 - SSH 連接失敗"
        except Exception as e:
            logger.error(f"處理設備 {device['name']} ({device['ip']}) 時發生未知錯誤: {e}", exc_info=True)
            failed_devices_summary[device['name']] = f"未知錯誤: {e}"
        finally:
            if ssh_client_for_dg:
                try:
                    ssh_client_for_dg.close()
                    logger.debug(f"確保 SSH client (Data Groups) for {device['name']} 已關閉。")
                except:
                    pass

    logger.info(f"更新週期完成: {overall_success_count}/{len(devices)} 台設備完全更新成功。")
    if failed_devices_summary:
        logger.warning("更新失敗或部分失敗的設備詳情:")
        for dev_name, reason in failed_devices_summary.items():
            logger.warning(f"- {dev_name}: {reason}")

def main():
    logger.info(f"F5 自動更新腳本 (Data Groups via SSH/TMSH, iRule via API) 已啟動。iRule 自動更新: {'啟用' if ENABLE_IRULE_AUTO_UPDATE else '停用'}")
    logger.info(f"rpzip_blacklist 管理: {'啟用' if MANAGE_RPZIP_BLACKLIST else '停用'}")
    create_f5_devices_file_example()
    logger.info(f"請確保 {F5_DEVICES_FILE} 和 iRule 範本 ({LOCAL_MASTER_IRULE_FILE}) 已配置。")
    logger.info(f"F5 密碼從環境變數讀取 (例如 F5_PASSWORD_F5_Device1)。")
    logger.info(f"目標 F5 iRule 名稱: {TARGET_IRULE_NAME_ON_F5}")
    logger.info(f"F5 API SSL 驗證: {'啟用' if F5_API_VERIFY_SSL else '停用 (不建議生產環境)'}")
    logger.info("-" * 30)
    try:
        update_all_devices()
        logger.info(f"腳本將每 {UPDATE_INTERVAL_SECONDS} 秒執行一次更新。按 Ctrl+C 可停止。")
        while True:
            next_run_time = datetime.fromtimestamp(time.time() + UPDATE_INTERVAL_SECONDS)
            logger.info(f"下次執行時間: {next_run_time.strftime('%Y-%m-%d %H:%M:%S')} (約 {(UPDATE_INTERVAL_SECONDS / 60):.0f} 分鐘後)")
            time.sleep(UPDATE_INTERVAL_SECONDS)
            update_all_devices()
    except KeyboardInterrupt:
        logger.info("接收到中斷信號，腳本已停止。")
    except Exception as e:
        logger.error(f"主循環執行過程中發生未預期的錯誤: {e}", exc_info=True)

if __name__ == "__main__":
    main()
