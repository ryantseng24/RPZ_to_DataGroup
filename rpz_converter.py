#!/usr/bin/env python3
# coding: utf-8

import os
import re
import time
import subprocess
import logging
import ipaddress
import threading
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

# --- 基本設定 ---
DNS_SERVER = "10.8.38.225"  # 更新為您的 DNS 伺服器 IP
# *** TSIG Key 設定 ***
# LAB Key 範例，客戶需更改為適用於其環境的正式 Key
# 如果 DNS 伺服器不需要 TSIG Key 進行 AXFR，請將此變數設為 None 或空字串 ""
TSIG_KEY_STRING = "hmac-sha256:rpztw:jXt2Kt0bZevOXrl9GKfGPw=="
# TSIG_KEY_STRING = None # 如果不需要 TSIG Key，取消註解此行並註解掉上面那行

UPDATE_INTERVAL = 5 * 60  # 更新間隔 (秒)，例如 5 * 60 = 5 分鐘
FQDN_ZONE_LIST_FILE = "rpz_fqdn_zone.txt" # FQDN Zone 列表檔案
IP_ZONE_LIST_FILE = "rpz_ip_zone.txt"     # IP Zone 列表檔案
OUTPUT_DIR = "f5_datagroups"              # 輸出 Data Group 檔案的目錄
HTTP_PORT = 8080                          # 提供 Data Group 檔案的 HTTP 服務端口
CHUNK_SIZE = 10000                        # 處理大量 IP 記錄時的分塊大小

# --- Landing IP 監控設定 ---
MONITORED_ZONE = "rpztw." # 要監控 Landing IP 的 Zone 名稱 (注意結尾的點)
KNOWN_LANDING_IPS_FILE = "known_landing_ips.txt" # 儲存已知 Landing IP 的檔案路徑
# 如果檔案不存在或為空，將使用下面的預設列表
DEFAULT_KNOWN_LANDING_IPS = {
    "34.102.218.71",
    "182.173.0.181",
    "112.121.114.76",
    "210.64.24.25",
    "210.69.155.3",
    "35.206.236.238"
}
# --- 設定是否自動更新 known_landing_ips.txt ---
AUTO_UPDATE_KNOWN_IPS_FILE = True # 設定為 True 以自動更新檔案，False 則僅發送郵件

# --- Email 通知設定 ---
ENABLE_EMAIL_NOTIFICATION = True # 是否啟用 Email 通知
SMTP_SERVER = "smtp.gmail.com"  # 您的 SMTP 伺服器地址
SMTP_PORT = 587                     # 您的 SMTP 伺服器端口 (通常是 587 for TLS, 465 for SSL, 25 for non-secure)
SMTP_USE_TLS = True                 # 是否使用 TLS 加密 (建議 True)
SMTP_USER = "ryantseng0224@gmail.com" # 您的 SMTP 登入帳號
# 從環境變數讀取 SMTP 密碼
SMTP_PASSWORD_ENV_VAR = "SMTP_APP_PASSWORD" # 環境變數名稱
SMTP_PASSWORD = os.environ.get(SMTP_PASSWORD_ENV_VAR)
EMAIL_SENDER = "ryantseng0224@gmail.com" # 發件人 Email 地址
EMAIL_RECIPIENTS = ["ryan.tseng@uniforce.com.tw", "evan@uniforce.com.tw"] # 收件人 Email 地址列表

# --- 設定日誌 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("rpz_converter.log", encoding='utf-8'), # 指定 UTF-8 編碼
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("RPZ_Converter")

# --- 記錄 SMTP 密碼讀取狀態 ---
if ENABLE_EMAIL_NOTIFICATION:
    if not SMTP_PASSWORD:
        logger.warning(f"未找到 SMTP 密碼環境變數 {SMTP_PASSWORD_ENV_VAR}。Email 通知功能可能無法正常登入。")
    else:
        logger.info(f"已從環境變數 {SMTP_PASSWORD_ENV_VAR} 讀取 SMTP 密碼。")
if TSIG_KEY_STRING:
    logger.info(f"將使用 TSIG Key 進行 AXFR 查詢。金鑰名稱部分 (提示): {TSIG_KEY_STRING.split(':')[1] if ':' in TSIG_KEY_STRING else 'N/A'}")
else:
    logger.info("將不使用 TSIG Key 進行 AXFR 查詢。")


# --- 全域變數 ---
known_landing_ips = set()

# --- 函數定義 ---

def load_known_landing_ips():
    """從檔案載入已知的 Landing IP 清單到全域變數 known_landing_ips"""
    global known_landing_ips
    ips = set()
    if os.path.exists(KNOWN_LANDING_IPS_FILE):
        try:
            with open(KNOWN_LANDING_IPS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    ip = line.strip()
                    if ip and not ip.startswith('#'):
                        try:
                            ipaddress.ip_address(ip)
                            ips.add(ip)
                        except ValueError:
                            logger.warning(f"在 {KNOWN_LANDING_IPS_FILE} 中發現無效的 IP 地址: {ip}")
            logger.info(f"從 {KNOWN_LANDING_IPS_FILE} 載入了 {len(ips)} 個已知的 Landing IP。")
        except Exception as e:
            logger.error(f"讀取已知 Landing IP 檔案 {KNOWN_LANDING_IPS_FILE} 時發生錯誤: {e}")
            logger.info("將使用預設的 Known Landing IP 清單。")
            ips = DEFAULT_KNOWN_LANDING_IPS.copy()
    else:
        logger.info(f"未找到已知 Landing IP 檔案 {KNOWN_LANDING_IPS_FILE}。將使用預設清單。")
        ips = DEFAULT_KNOWN_LANDING_IPS.copy()
    known_landing_ips = ips

def save_known_landing_ips():
    """將全域變數 known_landing_ips 的內容儲存到檔案"""
    global known_landing_ips
    try:
        with open(KNOWN_LANDING_IPS_FILE, 'w', encoding='utf-8') as f:
            for ip in sorted(list(known_landing_ips)): # 從全域變數排序後寫入
                f.write(f"{ip}\n")
        logger.info(f"已將 {len(known_landing_ips)} 個 Landing IP 儲存到 {KNOWN_LANDING_IPS_FILE}")
    except Exception as e:
        logger.error(f"儲存已知 Landing IP 檔案 {KNOWN_LANDING_IPS_FILE} 時發生錯誤: {e}")

def send_email_notification(subject_prefix, changed_ips_message):
    """發送 Email 通知"""
    if not ENABLE_EMAIL_NOTIFICATION:
        logger.info("Email 通知功能已停用。")
        return
    if not EMAIL_RECIPIENTS:
        logger.warning("未設定 Email 收件人，無法發送通知。")
        return
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning(f"SMTP 用戶名或密碼 (來自環境變數 {SMTP_PASSWORD_ENV_VAR}) 未完整設定，無法登入 SMTP 伺服器發送郵件。")
        return

    subject = f"[RPZ Monitor] {subject_prefix} ({MONITORED_ZONE})"
    body_action_taken = ""
    if AUTO_UPDATE_KNOWN_IPS_FILE:
        body_action_taken = f"腳本已自動更新 {KNOWN_LANDING_IPS_FILE} 檔案。"
    else:
        body_action_taken = f"請手動檢查並更新 {KNOWN_LANDING_IPS_FILE} 檔案 (如果需要讓更新腳本處理這些變化)。"

    body = f"""
您好，

RPZ 轉換器在監控的 Zone '{MONITORED_ZONE}' 中偵測到 Landing IP 變化：

{changed_ips_message}

{body_action_taken}
請檢查 RPZ Server 設定並考慮是否需要更新 F5 iRule 設定以處理這些變化。

目前的已知 Landing IP 清單 ({KNOWN_LANDING_IPS_FILE} 更新後，如果自動更新已啟用)：
{', '.join(sorted(list(known_landing_ips)))}

偵測時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

此郵件由 RPZ 轉換器自動發送。
"""
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = ", ".join(EMAIL_RECIPIENTS)

    try:
        logger.info(f"嘗試發送 Email 通知至: {', '.join(EMAIL_RECIPIENTS)}")
        server = None
        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            if SMTP_PORT == 465:
                 server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
            else:
                 server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
        
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENTS, msg.as_string())
        server.quit()
        logger.info("Email 通知已成功發送。")
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP 登入失敗: {e}. 請檢查 SMTP_USER ({SMTP_USER}) 和環境變數 {SMTP_PASSWORD_ENV_VAR} 是否正確設定。")
    except smtplib.SMTPServerDisconnected as e:
         logger.error(f"SMTP 伺服器意外斷開連接: {e}")
    except smtplib.SMTPConnectError as e:
         logger.error(f"無法連接到 SMTP 伺服器 {SMTP_SERVER}:{SMTP_PORT}: {e}")
    except smtplib.SMTPException as e:
        logger.error(f"發送 Email 時發生 SMTP 錯誤: {e}")
    except Exception as e:
        logger.error(f"發送 Email 時發生未知錯誤: {e}", exc_info=True)

def read_zone_list(file_path):
    """讀取 zone 列表檔案"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            zones = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        return zones
    except FileNotFoundError:
        logger.error(f"Zone 列表檔案不存在: {file_path}")
        return []
    except Exception as e:
        logger.error(f"讀取 zone 列表時發生錯誤: {e}")
        return []

def query_zone_data(zone_name):
    """
    使用 dig axfr 查詢 zone 資料。
    如果設定了 TSIG_KEY_STRING，則使用 TSIG Key 進行驗證。
    """
    try:
        cmd = ["dig", f"@{DNS_SERVER}", "axfr", zone_name]
        if TSIG_KEY_STRING: 
            cmd.extend(["-y", TSIG_KEY_STRING])
            logger.info(f"執行命令: dig @{DNS_SERVER} axfr {zone_name} -y <TSIG_KEY_HIDDEN>")
        else:
            logger.info(f"執行命令: {' '.join(cmd)}")

        env = os.environ.copy()
        env['LANG'] = 'C'
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300, env=env, encoding='utf-8', errors='ignore')
        
        lines = result.stdout.splitlines()
        logger.info(f"從區域 {zone_name} 獲取了 {len(lines)} 行數據")
        return result.stdout
    except FileNotFoundError:
        logger.error(f"找不到 'dig' 命令。請確保已安裝 dig 工具並且其路徑在系統的 PATH 環境變數中。")
        return ""
    except subprocess.CalledProcessError as e:
        stderr_output = e.stderr.lower() if e.stderr else ""
        if "tsig" in stderr_output and ("failed" in stderr_output or "bad key" in stderr_output or "bad time" in stderr_output):
             logger.error(f"查詢 zone {zone_name} 時發生 TSIG 驗證失敗。請檢查 DNS 伺服器設定以及腳本中的 TSIG_KEY_STRING 是否正確且與伺服器同步。")
        else:
             logger.error(f"查詢 zone {zone_name} 時發生錯誤 (命令返回非零值): {e}")
        logger.error(f"錯誤輸出: {e.stderr.strip() if e.stderr else 'N/A'}")
        return ""
    except subprocess.TimeoutExpired:
        logger.error(f"查詢 zone {zone_name} 超時 (超過 300 秒)")
        return ""
    except Exception as e:
        logger.error(f"執行 dig 命令時發生未知錯誤: {e}", exc_info=True)
        return ""

def parse_fqdn_records(zone_data, zone_name):
    """解析 FQDN 類型的 zone 資料"""
    ip_grouped_entries = {}
    current_landing_ips = set()
    zone_pattern_part = re.escape(zone_name.rstrip('.'))
    a_record_pattern = re.compile(
       r'^((?:[a-zA-Z0-9*-](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*)' + 
       zone_pattern_part + r'\.' +
       r'\s+\d+\s+IN\s+A\s+' +
       r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$'
    )
    normal_count = 0
    wildcard_count = 0
    for line in zone_data.splitlines():
        line = line.strip()
        if not line or line.startswith(';') or ' SOA ' in line or ' NS ' in line:
             continue
        match = a_record_pattern.match(line)
        if match:
            full_domain_part = match.group(1) + zone_pattern_part
            subdomain_part = match.group(1).rstrip('.')
            ip = match.group(2)
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                logger.warning(f"在 zone {zone_name} 中發現無效的 A 記錄 IP 地址: {ip} (行: {line})")
                continue
            current_landing_ips.add(ip)
            if ip not in ip_grouped_entries:
                ip_grouped_entries[ip] = set()
            if subdomain_part.startswith('*'):
                domain_to_store = "." + subdomain_part[2:] if len(subdomain_part) > 1 else "."
                wildcard_count += 1
            elif not subdomain_part:
                 logger.debug(f"跳過根域名記錄: {full_domain_part} -> {ip}")
                 continue
            else:
                domain_to_store = subdomain_part
                normal_count += 1
            ip_grouped_entries[ip].add(domain_to_store)
    logger.info(f"從區域 {zone_name} 解析了 {normal_count} 條普通 FQDN A 記錄和 {wildcard_count} 條通配符 FQDN A 記錄")
    return ip_grouped_entries, current_landing_ips

def reverse_ip_segment(ip_segment):
    """反轉 IP 段的順序"""
    parts = ip_segment.split('.')
    if len(parts) != 5:
        logger.warning(f"試圖反轉無效的 IP segment: {ip_segment}")
        return None
    try:
        prefix_len = int(parts[0])
        reversed_ip_parts = reversed(parts[1:])
        valid_parts = []
        for part in reversed_ip_parts:
            p_int = int(part)
            if 0 <= p_int <= 255:
                valid_parts.append(str(p_int))
            else:
                raise ValueError("IP part out of range")
        if len(valid_parts) != 4:
             raise ValueError("Incorrect number of IP parts")
        reversed_ip = '.'.join(valid_parts)
        ipaddress.ip_network(f"{reversed_ip}/{prefix_len}", strict=False)
        return f"{reversed_ip}/{prefix_len}"
    except (ValueError, IndexError) as e:
        logger.warning(f"反轉或驗證 IP segment '{ip_segment}' 時出錯: {e}")
        return None

def parse_ip_records(zone_data, zone_name):
    """解析 IP 類型的 zone 資料"""
    datagroup_entries = set()
    zone_pattern_part = re.escape(zone_name.rstrip('.'))
    pattern = re.compile(r'^([0-9.]+)\.rpz-ip\.' + zone_pattern_part + r'\.\s+\d+\s+IN\s+CNAME\s+\.$')
    count = 0
    for line in zone_data.splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
             continue
        match = pattern.match(line)
        if match:
            ip_segment = match.group(1)
            cidr = reverse_ip_segment(ip_segment)
            if cidr:
                try:
                    network = ipaddress.ip_network(cidr, strict=False)
                    if network.prefixlen == 32:
                        host_ip = str(network.network_address)
                        datagroup_entries.add(f"host {host_ip}")
                    else:
                        datagroup_entries.add(f"network {str(network)}")
                    count += 1
                except ValueError as e:
                    logger.warning(f"無效的 CIDR 格式 {cidr} (來自 {ip_segment}): {e}")
    logger.info(f"從區域 {zone_name} 解析了 {count} 條有效的 IP 記錄")
    return list(datagroup_entries)

def write_datagroup_file(entries, output_file):
    """將數據寫入 F5 datagroup 格式的檔案，每行末尾添加逗號"""
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            for entry in sorted(entries):
                entry_str = str(entry).strip()
                if ' ' in entry_str and not (entry_str.startswith("host ") or entry_str.startswith("network ")):
                     if not (entry_str.startswith('"') and entry_str.endswith('"')):
                          entry_str = f'"{entry_str}"'
                if ' := ' in entry_str:
                     parts = entry_str.split(' := ', 1)
                     key = parts[0].strip()
                     value = parts[1].strip()
                     if not (key.startswith('"') and key.endswith('"')) and (' ' in key or '.' in key or '*' in key):
                          key = f'"{key}"'
                     if not (value.startswith('"') and value.endswith('"')) and ' ' in value:
                          value = f'"{value}"'
                     entry_str = f"{key} := {value}"
                f.write(f"{entry_str},\n")
        logger.info(f"成功寫入 datagroup 檔案: {output_file}，包含 {len(entries)} 條記錄")
        return True
    except Exception as e:
        logger.error(f"寫入 datagroup 檔案 {output_file} 時發生錯誤: {e}", exc_info=True)
        return False

def write_domains_file(domains, output_file):
    """將域名列表寫入檔案，每行一個域名並添加逗號 (適用於 class match ends_with)"""
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            for domain in sorted(list(domains)):
                domain_str = str(domain).strip()
                if not (domain_str.startswith('"') and domain_str.endswith('"')):
                    if ' ' in domain_str or domain_str.startswith('.') or '*' in domain_str:
                        domain_str = f'"{domain_str}"'
                f.write(f"{domain_str},\n")
        logger.info(f"成功寫入域名列表檔案: {output_file}，包含 {len(domains)} 條記錄")
        return True
    except Exception as e:
        logger.error(f"寫入域名列表檔案 {output_file} 時發生錯誤: {e}", exc_info=True)
        return False

def process_fqdn_zones():
    """
    處理 FQDN 類型的 RPZ zones
    按 IP 地址分組創建域名列表，並保持 zone 信息
    同時監控指定 zone 的 Landing IP 變化，並可選擇自動更新 known_landing_ips.txt
    """
    global known_landing_ips # 允許修改全域變數
    zones = read_zone_list(FQDN_ZONE_LIST_FILE)
    if not zones:
        logger.warning(f"沒有在 {FQDN_ZONE_LIST_FILE} 中找到 zones")
        return

    all_kv_entries = []
    # 在處理所有 FQDN zone 之前，先載入一次 known_landing_ips
    # 這樣可以確保在比較時，known_landing_ips 是最新的（如果上個週期有更新）
    # 但更重要的是，run_conversion_cycle 每次開始時會呼叫 load_known_landing_ips()
    # 所以這裡的 known_landing_ips 應該是當前週期的初始狀態

    for zone in zones:
        if not zone.endswith('.'):
            logger.warning(f"Zone '{zone}' 在 {FQDN_ZONE_LIST_FILE} 中缺少結尾的點，已自動添加。")
            zone = zone + "."
        logger.info(f"處理 FQDN zone: {zone}")
        zone_data = query_zone_data(zone)
        if not zone_data:
            logger.warning(f"無法獲取 zone 資料: {zone}")
            continue
        ip_grouped_entries, current_zone_landing_ips = parse_fqdn_records(zone_data, zone)

        if zone == MONITORED_ZONE:
            logger.info(f"開始檢查監控的 Zone '{MONITORED_ZONE}' 的 Landing IP...")
            logger.info(f"從 Zone '{MONITORED_ZONE}' 發現的 Landing IP: {current_zone_landing_ips}")
            logger.info(f"目前已知的 Landing IP (來自 {KNOWN_LANDING_IPS_FILE} 或預設，在本次循環開始時載入): {known_landing_ips}")

            newly_found_ips = current_zone_landing_ips - known_landing_ips
            disappeared_ips = known_landing_ips - current_zone_landing_ips
            
            # 標記 known_landing_ips 是否發生了變化
            known_ips_changed = False

            if newly_found_ips:
                logger.warning(f"***** 在 Zone '{MONITORED_ZONE}' 發現新的 Landing IP: {newly_found_ips} *****")
                if AUTO_UPDATE_KNOWN_IPS_FILE:
                    logger.info(f"自動更新設定已啟用，將新的 Landing IP {newly_found_ips} 加入已知清單...")
                    known_landing_ips.update(newly_found_ips)
                    known_ips_changed = True
                send_email_notification("發現新的 Landing IP", f"新增的 IP: {', '.join(newly_found_ips)}")
            
            if disappeared_ips:
                logger.warning(f"***** 在 Zone '{MONITORED_ZONE}' 有 Landing IP 消失了: {disappeared_ips} *****")
                if AUTO_UPDATE_KNOWN_IPS_FILE:
                    logger.info(f"自動更新設定已啟用，將從已知清單中移除消失的 Landing IP {disappeared_ips}...")
                    known_landing_ips.difference_update(disappeared_ips)
                    known_ips_changed = True
                # 可以選擇是否為消失的 IP 發送通知，目前與新增的通知合併
                # send_email_notification("有 Landing IP 消失", f"消失的 IP: {', '.join(disappeared_ips)}")
            
            if known_ips_changed: # 如果 known_landing_ips 因新增或移除而改變
                save_known_landing_ips() # 將更新後的 known_landing_ips 寫回檔案
                logger.info(f"已將更新後的 Landing IP 列表儲存到 {KNOWN_LANDING_IPS_FILE}。")
            
            if not newly_found_ips and not disappeared_ips:
                logger.info(f"Zone '{MONITORED_ZONE}' 的 Landing IP 與已知清單一致，無需操作。")

        if ip_grouped_entries:
            zone_kv_entries = []
            # 為每個 IP 地址創建一個單獨的域名列表檔案 (ends_with 格式)
            # 這裡的 ip_grouped_entries 是基於 *當前*從 DNS 拉取的資料，所以只會包含有效的 Landing IP
            for ip, domains in ip_grouped_entries.items():
                if not domains: continue
                ip_filename = ip.replace(".", "_")
                zone_prefix = zone.rstrip('.').replace('.', '_')
                output_file_domain_list = os.path.join(OUTPUT_DIR, f"{zone_prefix}_{ip_filename}.txt")
                write_domains_file(domains, output_file_domain_list)
                for domain in domains:
                    key = str(domain).strip()
                    value = str(ip).strip()
                    if not (key.startswith('"') and key.endswith('"')) and (' ' in key or key.startswith('.') or '*' in key):
                         key = f'"{key}"'
                    zone_kv_entries.append(f"{key} := {value}")
            output_file_kv = os.path.join(OUTPUT_DIR, f"{zone.rstrip('.').replace('.', '_')}_fqdn_kv.txt")
            write_datagroup_file(zone_kv_entries, output_file_kv)
            all_kv_entries.extend(zone_kv_entries)
        else:
             logger.info(f"Zone {zone} 沒有解析到任何有效的 A 記錄，跳過檔案生成。")

    if all_kv_entries:
        merged_output_file_kv = os.path.join(OUTPUT_DIR, "rpz_blacklist_fqdn_kv.txt")
        write_datagroup_file(list(set(all_kv_entries)), merged_output_file_kv)
    else:
        logger.info("沒有找到任何 FQDN 記錄來創建合併的 Key/Value 檔案。")

def process_ip_zones():
    """處理 IP 類型的 RPZ zones，並將所有記錄合併到一個檔案"""
    zones = read_zone_list(IP_ZONE_LIST_FILE)
    if not zones:
        logger.warning(f"沒有在 {IP_ZONE_LIST_FILE} 中找到 zones")
        return
    all_entries = set()
    for zone in zones:
        if not zone.endswith('.'):
            logger.warning(f"Zone '{zone}' 在 {IP_ZONE_LIST_FILE} 中缺少結尾的點，已自動添加。")
            zone = zone + "."
        logger.info(f"處理 IP zone: {zone}")
        zone_data = query_zone_data(zone)
        if not zone_data:
            logger.warning(f"無法獲取 zone 資料: {zone}")
            continue
        entries = parse_ip_records(zone_data, zone)
        if not entries:
            logger.warning(f"在 zone {zone} 中沒有找到有效的 IP 記錄")
            continue
        all_entries.update(entries)
        output_file = os.path.join(OUTPUT_DIR, f"{zone.rstrip('.').replace('.', '_')}_ip.txt")
        write_datagroup_file(entries, output_file)
    if all_entries:
        merged_output_file = os.path.join(OUTPUT_DIR, "rpzip_blacklist.txt")
        write_datagroup_file(list(all_entries), merged_output_file)
    else:
        logger.info("沒有找到任何 IP 記錄來創建合併的 IP 黑名單檔案。")

class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    """自定義 HTTP 請求處理器，用於提供特定目錄下的檔案"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=OUTPUT_DIR, **kwargs)
    def log_message(self, format, *args):
        pass 
    def end_headers(self):
        path = self.translate_path(self.path)
        if path.endswith(".txt") and os.path.isfile(path):
            try:
                self.send_header('Content-type', 'text/plain; charset=utf-8')
            except ConnectionAbortedError:
                 pass
            except Exception as e:
                 logger.warning(f"設置 Content-Type for {self.path} 時出錯: {e}")
        super().end_headers()

def start_http_server():
    """啟動 HTTP 伺服器"""
    server_address = ('0.0.0.0', HTTP_PORT)
    try:
        HTTPServer.allow_reuse_address = True
        httpd = HTTPServer(server_address, CustomHTTPRequestHandler)
        logger.info(f"啟動 HTTP 伺服器在端口 {HTTP_PORT}，提供來自 '{OUTPUT_DIR}' 目錄的檔案")
        httpd.serve_forever()
    except OSError as e:
         if e.errno == 98:
              logger.error(f"HTTP 伺服器啟動失敗：端口 {HTTP_PORT} 已被佔用。請檢查是否有其他程序正在使用此端口。")
         else:
              logger.error(f"HTTP 伺服器啟動時發生 OS 錯誤: {e}")
    except Exception as e:
        logger.error(f"HTTP 伺服器發生錯誤: {e}", exc_info=True)

def run_conversion_cycle():
    """執行一次完整的轉換流程"""
    start_time = time.time()
    logger.info(f"開始轉換流程，時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        load_known_landing_ips() # 確保每次循環開始時載入最新的已知 IP
        process_fqdn_zones()
        process_ip_zones()
        elapsed_time = time.time() - start_time
        logger.info(f"轉換完成，用時: {elapsed_time:.2f} 秒")
    except Exception as e:
        logger.error(f"轉換過程中發生錯誤: {e}", exc_info=True)

def main():
    """主函數"""
    logger.info("啟動 RPZ 到 F5 Datagroup 轉換器 (含 Landing IP 監控)")
    logger.info(f"監控的 Zone: {MONITORED_ZONE}")
    logger.info(f"Email 通知: {'啟用' if ENABLE_EMAIL_NOTIFICATION else '停用'}")
    if AUTO_UPDATE_KNOWN_IPS_FILE:
        logger.info(f"將自動更新 {KNOWN_LANDING_IPS_FILE} 檔案當發現新的或移除舊的 Landing IP。")
    else:
        logger.info(f"將不會自動更新 {KNOWN_LANDING_IPS_FILE} 檔案，僅發送 Email 通知。")

    load_known_landing_ips() # 初始載入
    logger.info(f"初始載入的已知 Landing IP: {known_landing_ips}")
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    time.sleep(2)
    if not http_thread.is_alive() and HTTP_PORT != 0:
         logger.warning("HTTP 伺服器線程未能成功啟動。請檢查日誌中的錯誤訊息。")
    logger.info("執行初始轉換...")
    run_conversion_cycle()
    logger.info("初始轉換完成。")
    while True:
        logger.info(f"等待 {UPDATE_INTERVAL} 秒後進行下一次轉換...")
        time.sleep(UPDATE_INTERVAL)
        run_conversion_cycle()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("接收到中斷信號，程式退出")
    except Exception as e:
        logger.critical(f"發生未處理的嚴重異常，程式終止: {e}", exc_info=True)
