#!/usr/bin/env python3
# coding: utf-8

import os
import re
import time
import subprocess
import logging
import ipaddress
import threading
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("rpz_converter.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("RPZ_Converter")

# 配置变量
DNS_SERVER = "10.8.38.227"
UPDATE_INTERVAL = 5 * 60  # 5分钟，单位秒
FQDN_ZONE_LIST_FILE = "rpz_fqdn_zone.txt"
IP_ZONE_LIST_FILE = "rpz_ip_zone.txt"
OUTPUT_DIR = "f5_datagroups"
HTTP_PORT = 8080  # HTTP服务端口

# 确保输出目录存在
os.makedirs(OUTPUT_DIR, exist_ok=True)

def read_zone_list(file_path):
    """
    读取zone列表文件
    """
    try:
        with open(file_path, 'r') as f:
            zones = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        return zones
    except FileNotFoundError:
        logger.error(f"Zone列表文件不存在: {file_path}")
        return []
    except Exception as e:
        logger.error(f"读取zone列表时发生错误: {e}")
        return []

def query_zone_data(zone_name):
    """
    使用dig axfr查询zone数据
    """
    try:
        cmd = ["dig", f"@{DNS_SERVER}", "axfr", zone_name]
        logger.info(f"执行命令: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"查询zone {zone_name} 时发生错误: {e}")
        logger.error(f"错误输出: {e.stderr}")
        return ""
    except Exception as e:
        logger.error(f"执行dig命令时发生未知错误: {e}")
        return ""

def parse_fqdn_records(zone_data, zone_name):
    """
    解析FQDN类型的zone数据
    格式示例: aaa.aaa.rpztw. 28800 IN A 1.1.1.1
    转换为: aaa.aaa := 1.1.1.1
    """
    datagroup_entries = []
    pattern = re.compile(r'^([^.\s]+\.[^.\s]+)\.' + re.escape(zone_name) + r'\.\s+\d+\s+IN\s+A\s+(\d+\.\d+\.\d+\.\d+)$')
    
    for line in zone_data.splitlines():
        match = pattern.match(line.strip())
        if match:
            fqdn = match.group(1)
            ip = match.group(2)
            datagroup_entries.append(f"{fqdn} := {ip}")
    
    return datagroup_entries

def reverse_ip_segment(ip_segment):
    """
    反转IP段的顺序
    例如: 32.10.213.193.141 -> 141.193.213.10/32
    """
    parts = ip_segment.split('.')
    if len(parts) != 5:
        return None
    
    prefix_len = int(parts[0])
    reversed_ip = '.'.join(reversed(parts[1:]))
    
    return f"{reversed_ip}/{prefix_len}"

def parse_ip_records(zone_data, zone_name):
    """
    解析IP类型的zone数据
    格式示例: 32.10.213.193.141.rpz-ip.rpzip. 28800 IN CNAME .
    转换为: host 141.193.213.10, 或 network 23.42.102.0/24,
    """
    datagroup_entries = []
    # 使用正则表达式匹配IPv4 rpz-ip格式
    pattern = re.compile(r'^([0-9.]+)\.rpz-ip\.' + re.escape(zone_name) + r'\.\s+\d+\s+IN\s+CNAME\s+\.$')
    
    for line in zone_data.splitlines():
        match = pattern.match(line.strip())
        if match:
            ip_segment = match.group(1)
            cidr = reverse_ip_segment(ip_segment)
            if cidr:
                try:
                    # 验证CIDR格式
                    network = ipaddress.IPv4Network(cidr)
                    # 区分单个主机IP和网络
                    if network.prefixlen == 32:
                        # 单个主机IP (例如 141.193.213.10/32)
                        host_ip = str(network.network_address)
                        datagroup_entries.append(f"host {host_ip}")
                    else:
                        # 网络 (例如 23.42.102.0/24)
                        datagroup_entries.append(f"network {cidr}")
                except ValueError as e:
                    logger.warning(f"无效的CIDR格式 {cidr}: {e}")
    
    return datagroup_entries

def write_datagroup_file(entries, output_file):
    """
    将数据写入F5 datagroup格式的文件，每行末尾添加逗号
    """
    try:
        with open(output_file, 'w') as f:
            for entry in entries:
                f.write(f"{entry},\n")
        logger.info(f"成功写入datagroup文件: {output_file}")
        return True
    except Exception as e:
        logger.error(f"写入datagroup文件时发生错误: {e}")
        return False

def process_fqdn_zones():
    """
    处理FQDN类型的RPZ zones，并将所有记录合并到一个文件
    """
    zones = read_zone_list(FQDN_ZONE_LIST_FILE)
    if not zones:
        logger.warning(f"没有在 {FQDN_ZONE_LIST_FILE} 中找到zones")
        return
    
    all_entries = []
    
    for zone in zones:
        logger.info(f"处理FQDN zone: {zone}")
        zone_data = query_zone_data(zone)
        
        if not zone_data:
            logger.warning(f"无法获取zone数据: {zone}")
            continue
        
        entries = parse_fqdn_records(zone_data, zone)
        
        if not entries:
            logger.warning(f"在zone {zone} 中没有找到有效的FQDN记录")
            continue
        
        # 将当前zone的记录添加到所有记录列表中
        all_entries.extend(entries)
        
        # 同时为每个单独的zone创建文件（保留原功能）
        output_file = os.path.join(OUTPUT_DIR, f"{zone.replace('.', '_')}_fqdn.txt")
        write_datagroup_file(entries, output_file)
    
    # 创建合并后的文件
    if all_entries:
        merged_output_file = os.path.join(OUTPUT_DIR, "rpz_blacklist.txt")
        write_datagroup_file(all_entries, merged_output_file)
        logger.info(f"成功创建合并的FQDN记录文件: {merged_output_file}")

def process_ip_zones():
    """
    处理IP类型的RPZ zones，并将所有记录合并到一个文件
    """
    zones = read_zone_list(IP_ZONE_LIST_FILE)
    if not zones:
        logger.warning(f"没有在 {IP_ZONE_LIST_FILE} 中找到zones")
        return
    
    all_entries = []
    
    for zone in zones:
        logger.info(f"处理IP zone: {zone}")
        zone_data = query_zone_data(zone)
        
        if not zone_data:
            logger.warning(f"无法获取zone数据: {zone}")
            continue
        
        entries = parse_ip_records(zone_data, zone)
        
        if not entries:
            logger.warning(f"在zone {zone} 中没有找到有效的IP记录")
            continue
        
        # 将当前zone的记录添加到所有记录列表中
        all_entries.extend(entries)
        
        # 同时为每个单独的zone创建文件（保留原功能）
        output_file = os.path.join(OUTPUT_DIR, f"{zone.replace('.', '_')}_ip.txt")
        write_datagroup_file(entries, output_file)
    
    # 创建合并后的文件
    if all_entries:
        merged_output_file = os.path.join(OUTPUT_DIR, "rpzip_blacklist.txt")
        write_datagroup_file(all_entries, merged_output_file)
        logger.info(f"成功创建合并的IP记录文件: {merged_output_file}")

class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    """
    自定义HTTP请求处理器，用于提供特定目录下的文件
    """
    def __init__(self, *args, **kwargs):
        # 设置目录为输出目录
        super().__init__(*args, directory=OUTPUT_DIR, **kwargs)
    
    def log_message(self, format, *args):
        """重写日志方法，使用我们的日志器"""
        logger.info(f"{self.address_string()} - {format % args}")

def start_http_server():
    """
    启动HTTP服务器
    """
    server_address = ('', HTTP_PORT)
    httpd = HTTPServer(server_address, CustomHTTPRequestHandler)
    logger.info(f"启动HTTP服务器在端口 {HTTP_PORT}")
    
    try:
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"HTTP服务器发生错误: {e}")

def main():
    """
    主函数
    """
    logger.info("启动RPZ到F5 Datagroup转换器")
    
    # 在单独的线程中启动HTTP服务器
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    logger.info(f"HTTP服务器已在后台线程启动，访问地址: http://localhost:{HTTP_PORT}/")
    
    # 立即执行一次转换
    logger.info("执行初始转换")
    process_fqdn_zones()
    process_ip_zones()
    
    while True:
        # 等待下一次更新
        logger.info(f"等待 {UPDATE_INTERVAL} 秒后进行下一次转换")
        time.sleep(UPDATE_INTERVAL)
        
        start_time = time.time()
        logger.info(f"开始转换流程，时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        process_fqdn_zones()
        process_ip_zones()
        
        elapsed_time = time.time() - start_time
        logger.info(f"转换完成，用时: {elapsed_time:.2f} 秒")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("接收到中断信号，程序退出")
    except Exception as e:
        logger.critical(f"发生未处理的异常: {e}", exc_info=True)
