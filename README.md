# RPZ 至 F5 Data Group 自動化專案

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)


## 專案描述

本專案旨在自動化處理 RPZ (Response Policy Zone) 資料，將其轉換為適用於 F5 BIG-IP 的外部 Data Group 檔案格式，並自動觸發 F5 設備更新這些 Data Group 的來源，以實現基於 RPZ 的 DNS 防火牆或策略路由。

主要流程包含：

1. 定期透過 dig axfr 從指定的 DNS 伺服器獲取 RPZ zone 資料。
2. 解析 FQDN (域名) 和 IP (網段/主機) 類型的 RPZ 記錄。
3. 針對 FQDN 記錄，根據其解析到的 Landing IP 地址進行分類，產生對應的 Data Group 檔案 (適用於 iRule 中按 Landing IP 處理的邏輯)。
4. 監控特定 FQDN Zone (例如 rpztw.) 的 Landing IP 是否出現變化，並在發現新 IP 時發送 Email 通知。
5. 針對 IP 記錄，產生包含 host 或 network 格式的 Data Group 檔案。
6. 產生合併後的 IP 黑名單檔案和 FQDN 黑名單 Key/Value 檔案。
7. 啟動一個本地 HTTP 伺服器，提供產生的 Data Group 檔案供 F5 下載。
8. 定期透過 SSH 連接多台 F5 設備，執行 tmsh 指令，更新 F5 上設定的外部 Data Group 的 source-path，使其指向本地 HTTP 伺服器提供的最新檔案。

## 系統架構

![RPZ to F5 DataGroup Automation Flow](APP_Flow.png)

## 專案組件

本專案主要由以下幾個部分組成：

1. **RPZ 轉換器腳本 (rpz_converter.py)**:
   - 核心處理邏輯，負責獲取、解析 RPZ 資料，產生 Data Group 檔案。
   - 內含 Landing IP 監控與 Email 通知功能。
   - 內建簡易 HTTP 伺服器，用於提供產生的檔案。
   - 應作為背景服務持續運行。

2. **F5 更新腳本 (dynamic_f5_updater.py)**:
   - 負責讀取 F5 設備列表和登入憑證（來自環境變數）。
   - 動態產生需要更新的 Data Group 列表及其對應的檔案 URL。
   - 透過 SSH 連接 F5 並執行 tmsh 指令 (管理 Data Group) 或透過 iControl REST API (管理 iRule)。
   - 應作為背景服務定期運行。

3. **F5 iRule (dns_rpz_irule.tcl)**:
   - 實際在 F5 上執行的 DNS 流量處理邏輯。
   - 使用 class match 或 matchclass 指令，根據轉換器產生的外部 Data Group 內容來判斷 DNS 查詢並執行相應動作（例如攔截、改寫回應等）。
   - 其 dg_ip_map 或類似邏輯需要與轉換器產生的 Data Group 名稱保持一致。

4. **設定檔**:
   - rpz_fqdn_zone.txt: 定義需要處理的 FQDN 類型的 RPZ Zone 列表。
   - rpz_ip_zone.txt: 定義需要處理的 IP 類型的 RPZ Zone 列表。
   - f5_devices.txt: 定義需要更新的 F5 設備 IP、登入用戶名和設備名稱（**不含密碼**）。
   - known_landing_ips.txt (可選): 儲存已知的 Landing IP，用於監控和檔案產生。若不存在，腳本會使用預設值，並可配置為自動更新。

5. **輸出目錄 (f5_datagroups/)**:
   - 由 rpz_converter.py 自動建立，存放所有產生的 Data Group .txt 檔案。
   - HTTP 伺服器會以此目錄作為根目錄。

6. **日誌檔**:
   - rpz_converter.log: 轉換器腳本的運行日誌。
   - f5_updater.log: F5 更新腳本的運行日誌。
   - (若使用 Systemd) journalctl: Systemd 服務的標準輸出和錯誤日誌。

## 近期主要功能更新 (摘要)

在過去數週的開發與測試中，本專案新增及完善了以下主要功能：

1. **TSIG Key 支援**:
   - rpz_converter.py 現在支援使用 TSIG Key 來進行 DNS Zone Transfer (AXFR) 驗證，增強了從 DNS 伺服器獲取資料的安全性。相關 Key 字串可在腳本中設定。

2. **密碼管理改進 (環境變數)**:
   - **SMTP 密碼**: rpz_converter.py 中的 Email 通知功能，其 SMTP 登入密碼已改為從環境變數 (SMTP_APP_PASSWORD) 讀取，避免密碼硬編碼在腳本中。
   - **F5 設備密碼**: dynamic_f5_updater.py 腳本現在從環境變數 (例如 F5_PASSWORD_F5_Device1) 讀取各 F5 設備的登入密碼，取代了原先可能將密碼存於設定檔的方式。

3. **Landing IP 自動化管理 (known_landing_ips.txt)**:
   - rpz_converter.py 能夠監控指定的 RPZ Zone (MONITORED_ZONE)。
   - 當偵測到新的 Landing IP 時，可配置為自動將新 IP 加入 known_landing_ips.txt。
   - 同時，也能偵測已不再使用的 Landing IP，並可配置為自動從 known_landing_ips.txt 中移除這些失效的 IP。
   - 此檔案的準確性是後續自動化 Data Group 和 iRule 更新的基礎。

4. **F5 Data Group 自動化管理增強**:
   - dynamic_f5_updater.py 現在會根據最新的 known_landing_ips.txt 和 Zone 設定檔，動態產生需要管理的 Data Group 列表。
   - **自動建立 Data Group**: 如果 F5 上不存在腳本預期要更新的外部 Data Group 物件，腳本會嘗試自動使用 tmsh create ltm data-group external ... 指令來建立它，然後再更新其 source-path。
   - **修正建立語法**: 針對不同 TMOS 版本，優化了 tmsh create 指令的語法，採用更通用的方式（先建立空物件再修改，或在 create 時直接指定 source-path 並帶有回退機制）。

5. **iRule 自動更新改用 iControl REST API**:
   - dynamic_f5_updater.py 更新 F5 iRule 的方式已從原先透過 SSH 執行 tmsh modify rule ... definition ... 改為使用 **F5 iControl REST API**。
   - 腳本會讀取一個本地的 iRule 範本檔案 (dns_rpz_irule_template.tcl)。
   - 根據最新的 known_landing_ips.txt 動態產生 iRule 中的 dg_ip_map (Data Group 與 Landing IP 的對應列表)。
   - 將包含最新 dg_ip_map 的完整 iRule 內容，透過 HTTPS PATCH 請求發送到 F5 的 API 端點，實現 iRule 的更新。
   - 這種方式能更可靠地處理包含多行內容、特殊字元及非 ASCII 字元 (如中文註解，雖然建議移除) 的 iRule，避免了命令列解析問題。

6. **詳細的安裝與設定 SOP**:
   - 建立了一份完整的標準作業程序 (SOP)，涵蓋在新 Ubuntu 伺服器上從頭開始安裝、設定並執行這兩個腳本的所有步驟，包括用戶建立、依賴安裝、檔案放置、權限設定、環境變數設定 (透過 Systemd)、Systemd 服務建立、防火牆設定及監控帳號設定。

7. **錯誤處理與日誌記錄優化**:
   - 在兩個腳本中都增強了錯誤捕捉和日誌記錄，方便追蹤問題和了解腳本運行狀態。

8. **可配置的功能開關**:
   - 加入了如 AUTO_UPDATE_KNOWN_IPS_FILE (是否自動更新 Landing IP 列表檔案)、ENABLE_IRULE_AUTO_UPDATE (是否啟用 iRule 自動更新)、MANAGE_RPZIP_BLACKLIST (是否管理特定的合併 Data Group) 等設定開關，讓使用者可以根據需求調整自動化程度。

## 環境需求 (Prerequisites)

- **作業系統:** Ubuntu LTS (建議 20.04 或更新版本)
- **Python:** Python 3.x (建議 3.6+)
- **系統工具:** python3-pip, python3-venv, dnsutils (提供 dig 指令)
- **Python 函式庫:** paramiko (用於 SSH 連線), requests (用於 iControl REST API)
- **Git:** (用於從 GitHub Clone 專案及版本控制)
- **網路連線:** 伺服器需可連線至 DNS 伺服器、SMTP 伺服器、目標 F5 設備，且 F5 設備需可訪問此伺服器的 HTTP 端口。
- **安裝權限:** 執行安裝與設定步驟的人員，需要具有該 Ubuntu 伺服器的 sudo 權限 (用於建立用戶、安裝套件、設定服務等)。運行腳本的 rpz_user 帳號本身**不應**具有 sudo 權限。

## 安裝與設定 (SOP)

以下是在全新的 Ubuntu LTS 伺服器上安裝、設定並執行本專案的詳細步驟。

### 步驟 1：建立用戶帳號

為了安全起見，我們不直接使用 root 或您個人的管理帳號來執行腳本。建立兩個專用帳號：

- rpz_user: 用於實際執行兩個 Python 腳本。
- rpz_monitor: 用於查看日誌和狀態（唯讀）。

```bash
# 建立用戶組 (如果不存在)
sudo groupadd rpzusers || logger -t SOP "Group rpzusers already exists."

# 建立執行用戶 rpz_user，不建立家目錄下的 mail spool file，指定主要群組
sudo useradd -m -s /bin/bash -g rpzusers rpz_user

# 建立監控用戶 rpz_monitor，不建立家目錄下的 mail spool file，指定主要群組
sudo useradd -m -s /bin/bash -g rpzusers rpz_monitor

# (可選) 為用戶設定密碼，如果需要讓他們可以透過密碼登入
# sudo passwd rpz_user
# sudo passwd rpz_monitor
# 或者，建議配置 SSH 金鑰登入，安全性更高
```

### 步驟 2：安裝系統依賴套件

安裝 Python3、pip（Python 套件管理器）、venv（虛擬環境工具）以及 dig 指令（包含在 dnsutils 中）。

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv dnsutils
```

### 步驟 3：建立專案目錄與虛擬環境

建立一個目錄來存放所有相關檔案，並在其中建立 Python 虛擬環境。

```bash
# 建立主目錄，例如放在 /opt 下
sudo mkdir /opt/rpz_project
sudo chown rpz_user:rpzusers /opt/rpz_project
sudo chmod 775 /opt/rpz_project # 允許群組成員寫入 (方便管理)

# 切換到 rpz_user 身份來建立虛擬環境
sudo -iu rpz_user

# 進入專案目錄
cd /opt/rpz_project

# 建立 Python 虛擬環境 (命名為 venv)
python3 -m venv venv

# 啟動虛擬環境 (之後安裝 Python 套件會裝在這裡)
# 注意：每次需要手動執行 Python 腳本或安裝套件時，都需要先啟動
# 但透過 systemd 執行時，會在服務設定中指定路徑，無需手動啟動
source venv/bin/activate

# (可選) 退出 rpz_user 身份，回到您原本的 sudo 用戶
# exit
```

### 步驟 4：安裝 Python 依賴套件

在 rpz_user 身份下，並**啟動虛擬環境**後，安裝 Python 函式庫。

```bash
# 確保您是以 rpz_user 身份並已啟動 venv
# (提示符號應該類似 (venv) rpz_user@hostname:/opt/rpz_project$)
pip install paramiko requests

# (可選) 退出虛擬環境 (如果不需要立即執行其他 pip 指令)
# deactivate
```

### 步驟 5：放置腳本與設定檔

將以下檔案放置到 /opt/rpz_project/ 目錄下：

- rpz_converter.py (最新版本，支援 TSIG 和自動更新 known_landing_ips.txt)
- dynamic_f5_updater.py (最新版本，支援自動建立 DG 和 API 更新 iRule)
- dns_rpz_irule_template.tcl (您的 iRule 範本，包含 # START_DG_IP_MAP_BLOCK 和 # END_DG_IP_MAP_BLOCK 標記)
- rpz_fqdn_zone.txt (包含 FQDN Zone 列表)
- rpz_ip_zone.txt (包含 IP Zone 列表)
- f5_devices.txt (包含 F5 IP, 用戶名, 設備名 - **不含密碼**)
- (可選) known_landing_ips.txt (如果需要預先定義，否則腳本會使用預設值或自行創建)
- .gitignore (確保包含不應提交的檔案，如 *.log, f5_datagroups/, venv/ 等)

**設定所有權：** 確保這些檔案都屬於 rpz_user。

```bash
# 回到您的 sudo 用戶下執行
sudo chown -R rpz_user:rpzusers /opt/rpz_project
```

### 步驟 6：設定檔案權限

設定適當的權限。

```bash
# 腳本需要執行權限
sudo chmod +x /opt/rpz_project/rpz_converter.py
sudo chmod +x /opt/rpz_project/dynamic_f5_updater.py

# 設定檔通常只需要 rpz_user 可讀寫
# 讓同群組的 rpz_monitor 也能讀取
sudo chmod 664 /opt/rpz_project/*.txt
sudo chmod 664 /opt/rpz_project/*.tcl
sudo chmod 664 /opt/rpz_project/.gitignore
# 或者更嚴格，只讓 rpz_user 可讀寫
# sudo chmod 600 /opt/rpz_project/*.txt
# sudo chmod 600 /opt/rpz_project/f5_devices.txt # f5_devices.txt 權限可設更嚴格

# 確保 rpz_user 對目錄有寫入權限 (用於產生輸出檔和日誌)
sudo chmod 775 /opt/rpz_project
```

### 步驟 7：設定環境變數 (透過 Systemd)

我們不在 ~/.bashrc 中設定環境變數，而是直接在 Systemd 服務設定檔中定義，這樣更安全且只對服務本身可見。此步驟只是先了解需要設定哪些變數，實際設定在下一步。

**需要設定的變數 (由 rpz_user 執行的服務需要)：**

- F5_PASSWORD_F5_Device1 (設備名中的 - 已換成 _)
- F5_PASSWORD_F5_Device2 (設備名中的 - 已換成 _)
- ... (根據 f5_devices.txt 列出所有 F5)
- SMTP_APP_PASSWORD

### 步驟 8：設定 Systemd 服務

建立兩個 Systemd unit file 來管理這兩個腳本。

- **建立 rpz-converter.service:**
  
  ```bash
  sudo nano /etc/systemd/system/rpz-converter.service
  ```
  
  貼入以下內容 (請根據實際情況修改 WorkingDirectory, ExecStart 中的路徑, 以及 Environment 中的密碼):
  ```ini
  [Unit]
  Description=RPZ to F5 Data Group Converter Service
  After=network.target
  
  [Service]
  User=rpz_user
  Group=rpzusers
  WorkingDirectory=/opt/rpz_project
  # 設定環境變數 (在這裡設定比 .bashrc 更安全)
  Environment="SMTP_APP_PASSWORD=<Your Actual SMTP App Password>"
  # 如果環境變數太多，可以考慮使用 EnvironmentFile=/opt/rpz_project/.env
  ExecStart=/opt/rpz_project/venv/bin/python3 /opt/rpz_project/rpz_converter.py
  Restart=always
  RestartSec=10
  StandardOutput=journal # 將輸出導向 systemd journal
  StandardError=journal # 將錯誤導向 systemd journal
  
  [Install]
  WantedBy=multi-user.target
  ```
  
  **注意：** 請將 <Your Actual SMTP App Password> 替換為真實的密碼。

- **建立 f5-updater.service:**
  
  ```bash
  sudo nano /etc/systemd/system/f5-updater.service
  ```
  
  貼入以下內容 (請根據實際情況修改 WorkingDirectory, ExecStart 中的路徑, 以及 Environment 中的密碼):
  ```ini
  [Unit]
  Description=F5 External Data Group Updater Service
  After=network.target rpz-converter.service # 建議在 converter 啟動後再啟動
  
  [Service]
  User=rpz_user
  Group=rpzusers
  WorkingDirectory=/opt/rpz_project
  # 設定環境變數 (在這裡設定比 .bashrc 更安全)
  Environment="F5_PASSWORD_F5_Device1=<Password for F5_Device1>"
  Environment="F5_PASSWORD_F5_Device2=<Password for F5_Device2>"
  # ... 為 f5_devices.txt 中的每個設備添加一行 ...
  # 如果環境變數太多，可以考慮使用 EnvironmentFile=/opt/rpz_project/.env
  ExecStart=/opt/rpz_project/venv/bin/python3 /opt/rpz_project/dynamic_f5_updater.py
  Restart=always
  RestartSec=10
  StandardOutput=journal # 將輸出導向 systemd journal
  StandardError=journal # 將錯誤導向 systemd journal
  
  [Install]
  WantedBy=multi-user.target
  ```
  
  **注意：** 請將 <Password for F5_Device1> 等替換為對應 F5 設備的真實密碼 (名稱中的 - 需換成 _)。

- **重載 Systemd 並啟動服務：**
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable --now rpz-converter.service
  sudo systemctl enable --now f5-updater.service
  ```

- **檢查服務狀態：**
  ```bash
  sudo systemctl status rpz-converter.service
  sudo systemctl status f5-updater.service
  ```
  
  確認它們是 active (running) 狀態。

- **查看服務日誌：**
  ```bash
  sudo journalctl -u rpz-converter.service -f
  sudo journalctl -u f5-updater.service -f
  ```
  
  (-f 可以持續追蹤新日誌，按 Ctrl+C 停止)

### 步驟 9：設定防火牆 (以 UFW 為例)

根據需要開放端口。

```bash
# 允許 SSH (如果尚未允許)
sudo ufw allow ssh

# 允許 F5 設備訪問轉換器提供的 HTTP 服務 (假設 F5 IP 在 10.8.0.0/16 網段)
# 請根據實際 F5 IP 調整來源 IP 或網段
sudo ufw allow from 10.8.0.0/16 to any port 8080 proto tcp comment 'Allow F5 to fetch Data Groups'

# 啟用防火牆 (如果尚未啟用)
# sudo ufw enable
```

*注意：* 出站規則 (Outgoing) 通常預設是允許的，所以腳本訪問 DNS, SMTP, F5 SSH 通常不需要額外設定出站規則，除非您有非常嚴格的預設拒絕策略。

### 步驟 10：監控帳號設定

設定 rpz_monitor 帳號的權限。

- **讀取日誌檔案：**
  - 腳本產生的日誌檔案 (rpz_converter.log, f5_updater.log) 預設會寫在 /opt/rpz_project 目錄下，擁有者是 rpz_user。
  - 由於 rpz_monitor 和 rpz_user 都在 rpzusers 群組，且目錄和檔案權限設定為 664 或 775，rpz_monitor 應該可以直接讀取這些日誌。

  ```bash
  # 以 rpz_monitor 身份測試
  sudo -iu rpz_monitor
  cd /opt/rpz_project
  tail -f rpz_converter.log
  tail -f f5_updater.log
  exit
  ```

- **讀取 Systemd 日誌：**
  - 預設情況下，非特權用戶可能無法直接讀取所有 systemd 日誌。
  - 可以考慮將 rpz_monitor 加入 systemd-journal 群組，使其可以讀取更多日誌。

  ```bash
  sudo usermod -aG systemd-journal rpz_monitor
  # rpz_monitor 需要重新登入才能使群組變更生效
  ```

### 步驟 11：首次執行與驗證

1. **檢查服務狀態：** 使用 systemctl status 確認兩個服務都在運行。
2. **檢查 Systemd 日誌：** 使用 journalctl -u <service_name> 查看是否有啟動錯誤或運行中的錯誤訊息。
3. **檢查腳本自身日誌：** 查看 /opt/rpz_project/rpz_converter.log 和 /opt/rpz_project/f5_updater.log。
4. **檢查輸出目錄：** 查看 /opt/rpz_project/f5_datagroups/ 目錄下是否成功產生了 .txt 檔案。
5. **檢查 F5 更新：** 觀察 f5_updater.log 是否有成功連接 F5 並執行 tmsh modify 指令的記錄。登入 F5 檢查對應的外部 Data Group 的 Update Time 是否有更新。
6. **檢查 Email 通知：** （如果啟用了 Email）觸發一個 Landing IP 變更（例如手動修改 known_landing_ips.txt 再等轉換器運行），看看是否收到 Email。

## 使用方法

- **啟動服務:** sudo systemctl start rpz-converter.service f5-updater.service
- **停止服務:** sudo systemctl stop rpz-converter.service f5-updater.service
- **查看狀態:** sudo systemctl status rpz-converter.service f5-updater.service
- **開機自啟:** sudo systemctl enable rpz-converter.service f5-updater.service
- **取消開機自啟:** sudo systemctl disable rpz-converter.service f5-updater.service
- **查看日誌:**
  - Systemd 日誌: sudo journalctl -u rpz-converter.service -f 或 sudo journalctl -u f5-updater.service -f
  - 腳本自身日誌: tail -f /opt/rpz_project/rpz_converter.log 或 tail -f /opt/rpz_project/f5_updater.log (需要相應權限)

## F5 iRule 配置

本專案產生的 Data Group 檔案需要配合 F5 上的 iRule (dns_rpz_irule.tcl) 使用。

- **建立外部 Data Group:** 需要在 F5 上預先建立與 dynamic_f5_updater.py 腳本更新的 Data Group 名稱**完全一致**的**外部** Data Group 物件。例如：
  - rpztw_34_102_218_71 (類型: String)
  - rpztw_182_173_0_181 (類型: String)
  - ... (所有 rpztw 對應的 Landing IP)
  - phishtw_182_173_0_170 (類型: String)
  - rpzip_blacklist (類型: Address - 如果 F5 上實際名稱是這個)
  - ... (其他 IP Zone 對應的 Data Group，例如 some_ip_zone_ip)

- **設定 Source Path:** 這些外部 Data Group 的 source-path 會由 dynamic_f5_updater.py 腳本自動更新，指向轉換器提供的 HTTP URL。

- **iRule 邏輯:** iRule 需要使用 class match 或 matchclass 指令，配合 ends_with (用於 FQDN) 或 contains (用於 Address) 來查詢這些 Data Group。iRule 中的 dg_ip_map 變數的 Key 需要與 F5 上的 Data Group 名稱一致。

## 疑難排解 (Troubleshooting)

- **權限錯誤 (Permission Denied):** 檢查用戶 (rpz_user) 對專案目錄、腳本、設定檔、輸出目錄的讀寫執行權限是否正確。檢查 Systemd 服務設定的 User 和 Group。

- **環境變數未設定:** 檢查 Systemd 服務檔中的 Environment= 設定是否正確，密碼是否填寫。使用 systemctl show <service_name> | grep Environment 可以查看服務載入的環境變數（注意：這可能會暴露密碼，請謹慎使用）。

- **找不到 dig 指令:** 確保 dnsutils 套件已安裝 (sudo apt install dnsutils)。

- **rpz_converter.py 錯誤:** 檢查 rpz_converter.log 和 journalctl -u rpz-converter.service。常見問題：DNS 伺服器無法連線、dig axfr 失敗、設定檔讀取錯誤、磁碟空間不足、HTTP 端口被佔用。

- **dynamic_f5_updater.py 錯誤:** 檢查 f5_updater.log 和 journalctl -u f5-updater.service。常見問題：f5_devices.txt 格式錯誤、環境變數密碼未設定或錯誤、無法透過 SSH 連接 F5、F5 上的 Data Group 名稱不匹配或不存在、HTTP 伺服器無法訪問 (防火牆問題)、paramiko 或 requests 未安裝。

- **F5 Data Group 未更新:** 檢查 dynamic_f5_updater.py 日誌確認指令是否成功執行。檢查 F5 上的 Data Group Update Time。檢查 F5 是否能訪問轉換器提供的 HTTP URL (防火牆、網路路徑)。

- **404 File Not Found (F5 更新時):** 表示 rpz_converter.py 沒有產生 F5 預期要下載的那個 .txt 檔案。檢查 rpz_converter.py 的邏輯、輸入設定檔和日誌，確認檔案生成是否符合預期。檢查 dynamic_f5_updater.py 產生的 URL 是否正確。

- **iRule 更新失敗 (API):** 檢查 dynamic_f5_updater.py 日誌中關於 API 呼叫的錯誤訊息，確認 F5 IP、用戶名、密碼、iRule 名稱是否正確，F5 用戶是否有 API 權限，網路是否通暢，SSL 憑證是否被信任 (或已設定 F5_API_VERIFY_SSL = False 進行測試)。檢查 iRule 範本內容和標記註解是否正確。
