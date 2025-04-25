# RPZ to F5 DataGroup 轉換器

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

一個高效的中轉平台，用於將DNS RPZ區域資料轉換為F5 BIG-IP可用的DataGroup格式，並支援自動更新F5設定。本工具解決了F5設備直接處理RPZ區域資料時CPU負載過高的問題，透過預處理和最佳化格式顯著提升效能。

## 主要功能

- **RPZ資料轉換**：將DNS RPZ區域資料轉換為F5 DataGroup格式
  - 支援FQDN(域名)類型RPZ記錄：`aaa.aaa := 1.1.1.1`
  - 支援IP類型RPZ記錄：`host 141.193.213.10` 或 `network 23.42.102.0/24`
- **即時資料同步**：定期從DNS伺服器獲取最新RPZ資料
- **自動更新**：透過HTTP服務提供轉換後的資料檔案，支援自動更新F5設備
- **Landing IP監控**：監控並通知新出現的RPZ封鎖IP位址

## 系統架構

![RPZ to F5 DataGroup Automation Flow](APP_Flow.png)

### 資料流向說明

1. **AXFR區域轉移**：從DNS伺服器獲取RPZ區域資料
2. **載入設定**：載入轉換器設定檔
3. **產生檔案**：產生格式化的DataGroup檔案
4. **提供HTTP服務**：透過HTTP服務提供檔案存取
5. **存取DataGroup檔案**：F5更新器獲取轉換後的檔案
6. **透過HTTP存取**：F5更新器透過HTTP服務獲取資料
7. **更新F5設定**：將資料推送到F5 API
8. **應用到F5**：更新F5設備設定
9. **應用iRule邏輯**：在F5上實現RPZ處理邏輯
10. **處理客戶請求**：處理最終用戶的DNS請求

## 安裝指南

### 前提條件

- Python 3.6+
- BIND DNS伺服器(作為RPZ區域來源)
- F5 BIG-IP設備(支援外部DataGroup)
- 以下Python套件：
  - `paramiko` (用於SSH連接F5設備)
  - `ipaddress` (用於IP位址處理)

### 基本安裝步驟

1. 建立專用使用者帳號：

```bash
sudo groupadd rpzusers
sudo useradd -m -s /bin/bash -g rpzusers rpz_user
```

2. 安裝系統依賴套件：

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv dnsutils
```

3. 建立專案目錄與虛擬環境：

```bash
sudo mkdir /opt/rpz_project
sudo chown rpz_user:rpzusers /opt/rpz_project
sudo chmod 775 /opt/rpz_project

# 切換到rpz_user身份
sudo -iu rpz_user
cd /opt/rpz_project
python3 -m venv venv
source venv/bin/activate
pip install paramiko
```

4. 放置腳本和設定檔：

```bash
# 回到管理員帳號
exit
# 複製檔案到專案目錄
sudo cp rpz_converter.py dynamic_f5_updater.py rpz_fqdn_zone.txt rpz_ip_zone.txt f5_devices.txt /opt/rpz_project/
sudo chown -R rpz_user:rpzusers /opt/rpz_project
sudo chmod +x /opt/rpz_project/rpz_converter.py /opt/rpz_project/dynamic_f5_updater.py
```

5. 設定Systemd服務：

創建轉換器服務檔：
```bash
sudo nano /etc/systemd/system/rpz-converter.service
```

內容：
```
[Unit]
Description=RPZ to F5 Data Group Converter Service
After=network.target

[Service]
User=rpz_user
Group=rpzusers
WorkingDirectory=/opt/rpz_project
Environment="SMTP_APP_PASSWORD=<Email應用密碼>"
ExecStart=/opt/rpz_project/venv/bin/python3 /opt/rpz_project/rpz_converter.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

創建F5更新器服務檔：
```bash
sudo nano /etc/systemd/system/f5-updater.service
```

內容：
```
[Unit]
Description=F5 External Data Group Updater Service
After=network.target rpz-converter.service

[Service]
User=rpz_user
Group=rpzusers
WorkingDirectory=/opt/rpz_project
Environment="F5_PASSWORD_F5_Device1=<F5裝置1密碼>"
Environment="F5_PASSWORD_F5_Device2=<F5裝置2密碼>"
ExecStart=/opt/rpz_project/venv/bin/python3 /opt/rpz_project/dynamic_f5_updater.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

6. 啟用並啟動服務：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rpz-converter.service
sudo systemctl enable --now f5-updater.service
```

7. 檢查服務狀態：

```bash
sudo systemctl status rpz-converter.service
sudo systemctl status f5-updater.service
```

更詳細的安裝與設定指南，請參考[安裝SOP文件](安裝與設定RPZ轉換器及F5更新腳本SOP.md)。

## 設定檔說明

### rpz_fqdn_zone.txt
包含FQDN類型的RPZ區域列表，例如：
```
rpztw.
phishtw.
```

### rpz_ip_zone.txt
包含IP類型的RPZ區域列表，格式與`rpz_fqdn_zone.txt`相同。

### f5_devices.txt
包含F5設備的連線資訊，格式如下：
```
# 格式: IP地址,使用者名稱,設備名稱(可選)
10.8.34.186,admin,F5-Device1
# 10.1.1.2,admin,F5-Device2
```

注意：密碼透過環境變數設定，格式為`F5_PASSWORD_設備名稱`，例如`F5_PASSWORD_F5_Device1`。

## iRule設定

專案包含兩個範例iRule：

1. `dns_request_filiter.tcl`: 處理DNS請求的iRule
2. `irule_dns_response_filiter.tcl`: 處理DNS回應的iRule

## 故障排除

若遇到問題，請檢查以下日誌：

```bash
# 檢查轉換器服務日誌
sudo journalctl -u rpz-converter.service -f

# 檢查F5更新器服務日誌
sudo journalctl -u f5-updater.service -f

# 檢查腳本自身日誌
cat /opt/rpz_project/rpz_converter.log
cat /opt/rpz_project/f5_updater.log
```

## 授權條款

本專案使用MIT授權條款 - 詳見[LICENSE](LICENSE)檔案

## 聯絡方式

Ryan Tseng - ryan.tseng@uniforce.com.tw

專案連結: [https://github.com/ryantseng24/RPZ_to_DataGroup](https://github.com/ryantseng24/RPZ_to_DataGroup)
