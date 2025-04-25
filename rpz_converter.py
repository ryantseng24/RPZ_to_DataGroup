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
# *** 新增：TSIG Key 設定 ***
# 請將下面的 LAB Key 更換為您正式環境的 TSIG Key 字串
# 格式通常是 '演算法:金鑰名稱:金鑰內容(Base64)'
TSIG_KEY_STRING = "hmac-sha256:rpztw:jXt2Kt0bZevOXrl9GKfGPw==" # LAB Key 範例，客戶需更改為正式 Key
# 如果不需要 TSIG Key，請將此變數設為 None 或空字串 ""
# TSIG_KEY_STRING = None

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
        # 避免記錄密碼本身，只記錄讀取成功
        logger.info(f"已從環境變數 {SMTP_PASSWORD_ENV_VAR} 讀取 SMTP 密碼。")

# --- 全域變數 ---
# 使用集合來儲存已知 Landing IP，方便快速查找和比較
known_landing_ips = set()

# --- 函數定義 ---

def load_known_landing_ips():
    """從檔案載入已知的 Landing IP 清單"""
    global known_landing_ips
    ips = set()
    if os.path.exists(KNOWN_LANDING_IPS_FILE):
        try:
            with open(KNOWN_LANDING_IPS_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    ip = line.strip()
                    if ip and not ip.startswith('#'):
                        try:
                            # 驗證是否為合法的 IP 地址
                            ipaddress.ip_address(ip)
                            ips.add(ip)
                        except ValueError:
                            logger.warning(f"在 {KNOWN_LANDING_IPS_FILE} 中發現無效的 IP 地址: {ip}")
            logger.info(f"從 {KNOWN_LANDING_IPS_FILE} 載入了 {len(ips)} 個已知的 Landing IP。")
        except Exception as e:
            logger.error(f"讀取已知 Landing IP 檔案 {KNOWN_LANDING_IPS_FILE} 時發生錯誤: {e}")
            logger.info("將使用預設的 Known Landing IP 清單。")
            ips = DEFAULT_KNOWN_LANDING_IPS.copy() # 使用預設值
    else:
        logger.info(f"未找到已知 Landing IP 檔案 {KNOWN_LANDING_IPS_FILE}。將使用預設清單。")
        ips = DEFAULT_KNOWN_LANDING_IPS.copy() # 使用預設值

    known_landing_ips = ips

def save_known_landing_ips(ips_to_save):
    """將目前的 Landing IP 清單儲存到檔案"""
    try:
        with open(KNOWN_LANDING_IPS_FILE, 'w', encoding='utf-8') as f:
            for ip in sorted(list(ips_to_save)): # 排序後寫入
                f.write(f"{ip}\n")
        logger.info(f"已將 {len(ips_to_save)} 個 Landing IP 儲存到 {KNOWN_LANDING_IPS_FILE}")
    except Exception as e:
        logger.error(f"儲存已知 Landing IP 檔案 {KNOWN_LANDING_IPS_FILE} 時發生錯誤: {e}")


def send_email_notification(new_ips):
    """發送 Email 通知有新的 Landing IP"""
    if not ENABLE_EMAIL_NOTIFICATION:
        logger.info("Email 通知功能已停用。")
        return

    if not EMAIL_RECIPIENTS:
        logger.warning("未設定 Email 收件人，無法發送通知。")
        return

    subject = f"[RPZ Monitor] 發現新的 Landing IP 需要注意 ({MONITORED_ZONE})"
    body = f"""
您好，

RPZ 轉換器在監控的 Zone '{MONITORED_ZONE}' 中發現了以下新的 Landing IP：

{', '.join(new_ips)}

這些 IP 目前不在 iRule ({KNOWN_LANDING_IPS_FILE} 或預設清單) 的已知清單中。
請檢查 RPZ Server 設定並考慮是否需要更新 F5 iRule 設定以處理這些新的 IP。

目前的已知 Landing IP 清單：
{', '.join(sorted(list(known_landing_ips)))}

偵測時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

此郵件由 RPZ 轉換器自動發送。
"""
    msg = MIMEText(body, 'plain', 'utf-8') # 指定 UTF-8 編碼
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = ", ".join(EMAIL_RECIPIENTS)

    try:
        logger.info(f"嘗試發送 Email 通知至: {', '.join(EMAIL_RECIPIENTS)}")
        server = None # 初始化 server 變數
        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
             # 根據是否需要 SSL 選擇不同的類別
            if SMTP_PORT == 465: # Typically SSL port
                 server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
            else: # Typically non-secure port
                 server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)

        # 如果需要登入 (檢查用戶名和從環境變數讀取的密碼是否存在)
        if SMTP_USER and SMTP_PASSWORD:
             logger.info("使用 SMTP 用戶名和從環境變數讀取的密碼進行登入...")
             server.login(SMTP_USER, SMTP_PASSWORD)
        elif SMTP_USER and not SMTP_PASSWORD:
             logger.warning(f"已設定 SMTP 用戶名但未找到環境變數 {SMTP_PASSWORD_ENV_VAR} 中的密碼，嘗試不登入發送...")
        else:
             logger.info("未設定 SMTP 用戶名，嘗試不登入發送...")


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
        with open(file_path, 'r', encoding='utf-8') as f: # 指定 UTF-8 編碼
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
        # 基礎命令
        cmd = ["dig", f"@{DNS_SERVER}", "axfr", zone_name]

        # *** 修改：檢查是否需要加入 TSIG Key ***
        if TSIG_KEY_STRING:
            cmd.extend(["-y", TSIG_KEY_STRING])
            logger.info(f"使用 TSIG Key 執行命令: {' '.join(cmd)}") # 顯示包含 -y 的指令 (但不顯示 Key 內容)
        else:
            logger.info(f"執行命令: {' '.join(cmd)}") # 顯示不含 Key 的指令

        # 增加超時和緩衝區設置以處理大型區域
        # 設定環境變數 LANG=C 確保輸出是英文，避免解析問題
        env = os.environ.copy()
        env['LANG'] = 'C'
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300, env=env, encoding='utf-8', errors='ignore') # 指定編碼並忽略錯誤

        # 記錄返回的數據行數
        lines = result.stdout.splitlines()
        logger.info(f"從區域 {zone_name} 獲取了 {len(lines)} 行數據")

        return result.stdout
    except FileNotFoundError:
        logger.error(f"找不到 'dig' 命令。請確保已安裝 dig 工具並且其路徑在系統的 PATH 環境變數中。")
        return ""
    except subprocess.CalledProcessError as e:
        # 檢查是否為 TSIG 相關錯誤
        stderr_output = e.stderr.lower() if e.stderr else ""
        if "tsig" in stderr_output and "failed" in stderr_output:
             logger.error(f"查詢 zone {zone_name} 時發生 TSIG 驗證失敗。請檢查 TSIG_KEY_STRING 設定是否正確。")
             logger.error(f"錯誤輸出: {e.stderr}")
        else:
             logger.error(f"查詢 zone {zone_name} 時發生錯誤 (命令返回非零值): {e}")
             logger.error(f"錯誤輸出: {e.stderr}")
        return ""
    except subprocess.TimeoutExpired:
        logger.error(f"查詢 zone {zone_name} 超時 (超過 300 秒)")
        return ""
    except Exception as e:
        logger.error(f"執行 dig 命令時發生未知錯誤: {e}")
        return ""

def parse_fqdn_records(zone_data, zone_name):
    """
    解析 FQDN 類型的 zone 資料
    格式示例:
    - aaa.aaa.rpztw. 28800 IN A 1.1.1.1
    - *.aaa.aaa.rpztw. 28800 IN A 1.1.1.1

    返回一個字典，key 為 IP 地址，value 為域名列表 (set)
    """
    ip_grouped_entries = {}
    current_landing_ips = set() # 用於收集此 zone 的所有 landing IP

    # 正規表達式匹配 A 記錄 (包括根域名和子域名)
    # 允許域名部分包含數字、字母、連字號
    # 處理 zone 名稱中的點號
    zone_pattern_part = re.escape(zone_name.rstrip('.')) # 移除結尾的點並轉義
    # 匹配域名本身 (允許只有 zone 名稱的情況，例如 rpztw. IN A 1.1.1.1)
    # 或者子域名.zone名稱 (例如 sub.rpztw. IN A 1.1.1.1)
    # 或者通配符.子域名.zone名稱 (例如 *.sub.rpztw. IN A 1.1.1.1)
    # 修正：確保能正確處理只有 zone 名稱的記錄
    a_record_pattern = re.compile(
       r'^((?:[a-zA-Z0-9*-](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*)' + # 匹配子域名部分 (包括 *)
       zone_pattern_part + r'\.' + # 匹配 zone 名稱
       r'\s+\d+\s+IN\s+A\s+' + # 匹配 TTL, IN, A
       r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$' # 匹配 IP 地址
    )

    normal_count = 0
    wildcard_count = 0

    for line in zone_data.splitlines():
        line = line.strip()
        if not line or line.startswith(';') or ' SOA ' in line or ' NS ' in line:
             continue # 忽略空行、註解、SOA、NS 記錄

        match = a_record_pattern.match(line)
        if match:
            full_domain_part = match.group(1) + zone_pattern_part # 完整的域名部分 (含 zone)
            subdomain_part = match.group(1).rstrip('.') # 子域名部分 (移除結尾的點)
            ip = match.group(2)

            # 驗證 IP 地址是否有效
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                logger.warning(f"在 zone {zone_name} 中發現無效的 A 記錄 IP 地址: {ip} (行: {line})")
                continue

            # 將此 IP 加入此 zone 的 Landing IP 集合
            current_landing_ips.add(ip)

            # 初始化 IP 分組 (如果不存在)
            if ip not in ip_grouped_entries:
                ip_grouped_entries[ip] = set() # 使用集合避免重複域名

            # 判斷是普通記錄還是通配符記錄，並決定儲存的域名格式
            if subdomain_part.startswith('*'):
                # 通配符記錄 *.sub.domain.zone -> 儲存 .sub.domain
                domain_to_store = "." + subdomain_part[2:] if len(subdomain_part) > 1 else "." # 處理只有 '*' 的情況
                wildcard_count += 1
            elif not subdomain_part:
                 # 根域名記錄 domain.zone -> 儲存 domain.zone (不適用 ends_with，但保留)
                 # 為了相容性，也可能需要處理這種情況，但 ends_with 主要用於子域名
                 # 根據 F5 Data Group 的需求，可能不需要儲存根域名本身
                 # domain_to_store = full_domain_part # 暫不加入 ends_with 列表
                 logger.debug(f"跳過根域名記錄: {full_domain_part} -> {ip}")
                 continue # ends_with 不適用於根域名本身
            else:
                # 普通子域名記錄 sub.domain.zone -> 儲存 sub.domain
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
        return None # 返回 None 而不是空字串

    try:
        prefix_len = int(parts[0])
        reversed_ip_parts = reversed(parts[1:])
        # 驗證反轉後的部分是否為有效的 IP 地址部分
        valid_parts = []
        for part in reversed_ip_parts:
            if 0 <= int(part) <= 255:
                valid_parts.append(part)
            else:
                raise ValueError("IP part out of range")
        if len(valid_parts) != 4:
             raise ValueError("Incorrect number of IP parts")

        reversed_ip = '.'.join(valid_parts)
        # 驗證最終的 CIDR
        ipaddress.ip_network(f"{reversed_ip}/{prefix_len}", strict=False)
        return f"{reversed_ip}/{prefix_len}"
    except (ValueError, IndexError) as e:
        logger.warning(f"反轉或驗證 IP segment '{ip_segment}' 時出錯: {e}")
        return None


def parse_ip_records(zone_data, zone_name):
    """解析 IP 類型的 zone 資料"""
    datagroup_entries = set() # 使用集合避免重複
    # 使用正規表達式匹配 IPv4 rpz-ip 格式
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
                    # 驗證 CIDR 格式
                    network = ipaddress.ip_network(cidr, strict=False) # Allow host addresses like /32
                    # 區分單個主機 IP 和網路
                    if network.prefixlen == 32:
                        # 單個主機 IP (例如 141.193.213.10/32)
                        host_ip = str(network.network_address)
                        datagroup_entries.add(f"host {host_ip}")
                    else:
                        # 網路 (例如 23.42.102.0/24)
                        datagroup_entries.add(f"network {str(network)}") # 使用 str(network) 確保格式正確
                    count += 1
                except ValueError as e:
                    logger.warning(f"無效的 CIDR 格式 {cidr} (來自 {ip_segment}): {e}")

    logger.info(f"從區域 {zone_name} 解析了 {count} 條有效的 IP 記錄")
    return list(datagroup_entries) # 返回列表

def write_datagroup_file(entries, output_file):
    """將數據寫入 F5 datagroup 格式的檔案，每行末尾添加逗號"""
    try:
        # 確保輸出目錄存在
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f: # 指定 UTF-8 編碼
            # 對 entries 排序確保輸出順序一致性
            for entry in sorted(entries):
                # 確保 entry 是字串，並且處理可能存在的引號問題
                entry_str = str(entry).strip()
                # 如果 entry 包含空格，且不是以 host 或 network 開頭，則加上引號
                if ' ' in entry_str and not (entry_str.startswith("host ") or entry_str.startswith("network ")):
                     # 如果 entry 已經有引號，避免重複添加
                     if not (entry_str.startswith('"') and entry_str.endswith('"')):
                          entry_str = f'"{entry_str}"'

                # 處理 key := value 格式的引號
                if ' := ' in entry_str:
                     parts = entry_str.split(' := ', 1)
                     key = parts[0].strip()
                     value = parts[1].strip()
                     # 如果 key 包含空格或特殊字符，加上引號
                     if not (key.startswith('"') and key.endswith('"')) and (' ' in key or '.' in key or '*' in key):
                          key = f'"{key}"'
                      # 如果 value 包含空格，加上引號
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
        # 確保輸出目錄存在
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f: # 指定 UTF-8 編碼
            # 對域名排序確保輸出順序一致性
            for domain in sorted(list(domains)): # 轉換為 list 再排序
                # 確保域名是字串且去除空白
                domain_str = str(domain).strip()
                 # 如果域名包含空格或特殊字符 (例如 * 或 . 開頭)，加上引號
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
    同時監控指定 zone 的 Landing IP 變化
    """
    global known_landing_ips # 允許修改全域變數

    zones = read_zone_list(FQDN_ZONE_LIST_FILE)
    if not zones:
        logger.warning(f"沒有在 {FQDN_ZONE_LIST_FILE} 中找到 zones")
        return

    all_kv_entries = [] # 用於合併的 key/value 格式

    for zone in zones:
        # 確保 zone 名稱以點結尾，以符合 dig 和解析邏輯
        if not zone.endswith('.'):
            logger.warning(f"Zone '{zone}' 在 {FQDN_ZONE_LIST_FILE} 中缺少結尾的點，已自動添加。")
            zone = zone + "."

        logger.info(f"處理 FQDN zone: {zone}")
        zone_data = query_zone_data(zone)

        if not zone_data:
            logger.warning(f"無法獲取 zone 資料: {zone}")
            continue

        # 解析並按 IP 分組，同時獲取此 zone 的 landing IP
        ip_grouped_entries, current_zone_landing_ips = parse_fqdn_records(zone_data, zone)

        if not ip_grouped_entries:
            logger.warning(f"在 zone {zone} 中沒有找到有效的 FQDN A 記錄")
            # continue # 即使沒有 A 記錄，也可能需要檢查 Landing IP 是否都消失了

        # --- Landing IP 監控邏輯 ---
        if zone == MONITORED_ZONE:
            logger.info(f"開始檢查監控的 Zone '{MONITORED_ZONE}' 的 Landing IP...")
            logger.info(f"從 Zone '{MONITORED_ZONE}' 發現的 Landing IP: {current_zone_landing_ips}")
            logger.info(f"目前已知的 Landing IP ({KNOWN_LANDING_IPS_FILE} 或預設): {known_landing_ips}")

            # 找出新增的 IP (存在於 current，但不存在於 known)
            newly_found_ips = current_zone_landing_ips - known_landing_ips
            # 找出消失的 IP (存在於 known，但不存在於 current)
            # disappeared_ips = known_landing_ips - current_zone_landing_ips # 暫不處理消失的 IP

            if newly_found_ips:
                logger.warning(f"***** 在 Zone '{MONITORED_ZONE}' 發現新的 Landing IP: {newly_found_ips} *****")
                send_email_notification(newly_found_ips)
                # 可選：自動將新 IP 加入已知列表並儲存 (如果需要自動更新)
                # logger.info("自動將新的 Landing IP 加入已知清單...")
                # known_landing_ips.update(newly_found_ips)
                # save_known_landing_ips(known_landing_ips)
            else:
                logger.info(f"Zone '{MONITORED_ZONE}' 的 Landing IP 與已知清單一致，無需通知。")

            # if disappeared_ips:
            #     logger.warning(f"***** 在 Zone '{MONITORED_ZONE}' 有 Landing IP 消失了: {disappeared_ips} *****")
            #     # 可以選擇是否為消失的 IP 發送通知
            #     # send_disappeared_ip_notification(disappeared_ips)
            #     # 可選：自動從已知列表中移除消失的 IP
            #     # logger.info("自動從已知清單中移除消失的 Landing IP...")
            #     # known_landing_ips.difference_update(disappeared_ips)
            #     # save_known_landing_ips(known_landing_ips)


        # --- 產生 Data Group 檔案 ---
        if ip_grouped_entries:
            zone_kv_entries = [] # 該 zone 的 key/value 格式
            # 為每個 IP 地址創建一個單獨的域名列表檔案 (ends_with 格式)
            for ip, domains in ip_grouped_entries.items():
                if not domains: continue # 如果某個 IP 沒有對應的域名，跳過

                # 格式化 IP 以用於檔名 (例如: 34_102_218_71)
                ip_filename = ip.replace(".", "_")
                # 使用 zone 名稱作為前綴 (移除結尾的點)
                zone_prefix = zone.rstrip('.').replace('.', '_')
                output_file_domain_list = os.path.join(OUTPUT_DIR, f"{zone_prefix}_{ip_filename}.txt")

                # 寫入域名列表檔案 (ends_with 格式)
                write_domains_file(domains, output_file_domain_list)
                # logger.info(f"成功創建 zone {zone} IP {ip} 的域名列表檔案: {output_file_domain_list}，共 {len(domains)} 條記錄") # write_domains_file 內部已有日誌

                # 準備 key/value 格式數據
                for domain in domains:
                    # 確保 domain 和 ip 加上引號（如果需要）
                    key = str(domain).strip()
                    value = str(ip).strip()
                    if not (key.startswith('"') and key.endswith('"')) and (' ' in key or key.startswith('.') or '*' in key):
                         key = f'"{key}"'
                    # IP 通常不需要引號，除非 F5 有特殊要求
                    # if not (value.startswith('"') and value.endswith('"')) and ' ' in value:
                    #      value = f'"{value}"'
                    zone_kv_entries.append(f"{key} := {value}")

            # 為每個 zone 創建原始 key/value 格式檔案（用於可能的其他用途）
            output_file_kv = os.path.join(OUTPUT_DIR, f"{zone.rstrip('.').replace('.', '_')}_fqdn_kv.txt")
            write_datagroup_file(zone_kv_entries, output_file_kv)

            # 將當前 zone 的 key/value 記錄添加到合併列表中
            all_kv_entries.extend(zone_kv_entries)
        else:
             logger.info(f"Zone {zone} 沒有解析到任何有效的 A 記錄，跳過檔案生成。")


    # 創建合併的 key/value 格式檔案（用於 rpz_blacklist.txt）
    if all_kv_entries:
        merged_output_file_kv = os.path.join(OUTPUT_DIR, "rpz_blacklist_fqdn_kv.txt") # 檔名區分 IP 和 FQDN
        write_datagroup_file(list(set(all_kv_entries)), merged_output_file_kv) # 去重後寫入
        # logger.info(f"成功創建合併的 FQDN 記錄檔案 (KV 格式): {merged_output_file_kv}，共 {len(set(all_kv_entries))} 條記錄") # write_datagroup_file 內部已有日誌
    else:
        logger.info("沒有找到任何 FQDN 記錄來創建合併的 Key/Value 檔案。")


def process_ip_zones():
    """處理 IP 類型的 RPZ zones，並將所有記錄合併到一個檔案"""
    zones = read_zone_list(IP_ZONE_LIST_FILE)
    if not zones:
        logger.warning(f"沒有在 {IP_ZONE_LIST_FILE} 中找到 zones")
        return

    all_entries = set() # 使用集合避免重複

    for zone in zones:
        # 確保 zone 名稱以點結尾
        if not zone.endswith('.'):
            logger.warning(f"Zone '{zone}' 在 {IP_ZONE_LIST_FILE} 中缺少結尾的點，已自動添加。")
            zone = zone + "."

        logger.info(f"處理 IP zone: {zone}")
        zone_data = query_zone_data(zone)

        if not zone_data:
            logger.warning(f"無法獲取 zone 資料: {zone}")
            continue

        entries = parse_ip_records(zone_data, zone) # 返回的是 list

        if not entries:
            logger.warning(f"在 zone {zone} 中沒有找到有效的 IP 記錄")
            continue

        # 將當前 zone 的記錄添加到所有記錄集合中
        all_entries.update(entries) # 使用 update 將 list 加入 set

        # 為每個單獨的 zone 創建檔案 (host/network 格式)
        output_file = os.path.join(OUTPUT_DIR, f"{zone.rstrip('.').replace('.', '_')}_ip.txt")
        write_datagroup_file(entries, output_file)

    # 創建合併後的 IP 黑名單檔案 (host/network 格式)
    if all_entries:
        merged_output_file = os.path.join(OUTPUT_DIR, "rpzip_blacklist.txt")
        # 如果記錄太多，可以考慮分塊，但對於 IP 列表通常不需要
        # if len(all_entries) > CHUNK_SIZE: ...
        write_datagroup_file(list(all_entries), merged_output_file) # 轉換為 list 再寫入
        # logger.info(f"成功創建合併的 IP 記錄檔案: {merged_output_file}，共 {len(all_entries)} 條記錄") # write_datagroup_file 內部已有日誌
    else:
        logger.info("沒有找到任何 IP 記錄來創建合併的 IP 黑名單檔案。")


class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    """自定義 HTTP 請求處理器，用於提供特定目錄下的檔案"""
    def __init__(self, *args, **kwargs):
        # 設定目錄為輸出目錄
        super().__init__(*args, directory=OUTPUT_DIR, **kwargs)

    def log_message(self, format, *args):
        """重寫日誌方法，使用我們的日誌器"""
        # 避免記錄過多的 HTTP 請求日誌，除非需要調試
        # logger.info(f"{self.address_string()} - {format % args}")
        pass # 保持安靜

    # 可以添加更多自定義，例如設置 Content-Type
    def end_headers(self):
        # 為 .txt 檔案設置 text/plain; charset=utf-8
        path = self.translate_path(self.path) # 獲取實際文件路徑
        if path.endswith(".txt") and os.path.isfile(path): # 檢查文件是否存在
            try:
                # 嘗試發送 Content-Type，如果頭部已發送則忽略錯誤
                self.send_header('Content-type', 'text/plain; charset=utf-8')
            except ConnectionAbortedError:
                 # 連接已關閉，忽略
                 pass
            except Exception as e:
                 # 記錄其他潛在錯誤，但不中斷請求
                 logger.warning(f"設置 Content-Type for {self.path} 時出錯: {e}")
        super().end_headers()


def start_http_server():
    """啟動 HTTP 伺服器"""
    # 監聽所有接口
    server_address = ('0.0.0.0', HTTP_PORT)
    try:
        # 允許端口重用，避免 "Address already in use"
        HTTPServer.allow_reuse_address = True
        httpd = HTTPServer(server_address, CustomHTTPRequestHandler)
        logger.info(f"啟動 HTTP 伺服器在端口 {HTTP_PORT}，提供來自 '{OUTPUT_DIR}' 目錄的檔案")
        httpd.serve_forever()
    except OSError as e:
         if e.errno == 98: # Address already in use
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
        # 載入最新的已知 Landing IP (每次循環都載入，以便手動修改檔案後能生效)
        load_known_landing_ips()

        # 處理 FQDN Zones (包含 Landing IP 檢查)
        process_fqdn_zones()

        # 處理 IP Zones
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

    # 初始載入一次 Known Landing IPs
    load_known_landing_ips()
    logger.info(f"初始載入的已知 Landing IP: {known_landing_ips}")


    # 在單獨的線程中啟動 HTTP 伺服器
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    # 等待一小段時間確保 HTTP 伺服器有機會啟動或失敗
    time.sleep(2)
    if not http_thread.is_alive() and HTTP_PORT != 0:
         logger.warning("HTTP 伺服器線程未能成功啟動。請檢查日誌中的錯誤訊息。")


    # 立即執行一次轉換
    logger.info("執行初始轉換...")
    run_conversion_cycle()
    logger.info("初始轉換完成。")

    # 進入定時更新循環
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

