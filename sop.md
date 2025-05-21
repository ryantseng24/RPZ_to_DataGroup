# 安裝與設定 RPZ 轉換器及 F5 更新腳本 SOP

## 文件目的

本文件旨在提供在全新的 Ubuntu LTS 伺服器上，安裝、設定並執行 RPZ 轉換器 (rpz_converter.py) 及 F5 Data Group 與 iRule 更新腳本 (dynamic_f5_updater.py) 的標準作業程序。

## 前提條件

- 已安裝好一台 Ubuntu LTS 伺服器，建議最小化安裝以減少潛在弱點（建議 22.04 LTS 或更新版本）。

- 伺服器具有網路連線能力，可以：
  - 連接到指定的 DNS 伺服器 (執行 dig axfr，可能需要 TSIG Key)。
  - 連接到指定的 SMTP 伺服器 (發送 Email 通知)。
  - 連接到所有目標 F5 設備 (透過 SSH 執行 tmsh 指令進行 Data Group 管理，以及透過 HTTPS 訪問 iControl REST API 進行 iRule 更新)。
  - 被所有目標 F5 設備訪問（用於 F5 下載 Data Group 檔案的 HTTP 服務，預設端口 8080）。

- 執行此 SOP 文件進行**安裝與設定**的人員，需要具有該 Ubuntu 伺服器的 sudo 權限 (用於建立用戶、安裝套件、設定服務等)。運行腳本的 rpz_user 帳號本身**不應**具有 sudo 權限。

## 目標架構

- 在伺服器上建立專用、低權限的用戶 (rpz_user) 來執行腳本，以及一個監控用戶 (rpz_monitor)。
- 使用 Python 虛擬環境 (venv) 隔離依賴。
- 將 F5 設備密碼和 SMTP 密碼等敏感資訊儲存在環境變數中（透過 Systemd 服務設定）。
- 使用 Systemd 將兩個 Python 腳本設定為背景服務，確保持續運行和開機自動重啟。

## 詳細步驟

### 步驟 1：建立用戶帳號

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

安裝 Python3、pip、venv、dig (來自 dnsutils)、nano (編輯器) 和 ufw (防火牆工具)。

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv dnsutils nano ufw
```

### 步驟 3：建立專案目錄與虛擬環境

```bash
# 建立主目錄
sudo mkdir /opt/rpz_project
sudo chown rpz_user:rpzusers /opt/rpz_project
sudo chmod 775 /opt/rpz_project # 允許群組成員寫入

# 切換到 rpz_user 身份來建立虛擬環境
sudo -iu rpz_user

# 進入專案目錄
cd /opt/rpz_project

# 建立 Python 虛擬環境
python3 -m venv venv

# 啟動虛擬環境
source venv/bin/activate
# 提示符號應變為 (venv) rpz_user@...

# (可選) 退出 rpz_user 身份，回到您原本的 sudo 用戶
# exit
```

### 步驟 4：安裝 Python 依賴套件

在 rpz_user 身份下，並**啟動虛擬環境**後，安裝必要的 Python 函式庫。

```bash
# 確保您是以 rpz_user 身份並已啟動 venv
pip install paramiko requests

# (可選) 退出虛擬環境
# deactivate
```

### 步驟 5：放置腳本與設定檔

將以下檔案放置到 /opt/rpz_project/ 目錄下：

- rpz_converter.py (最新版本，支援 TSIG 和自動更新 known_landing_ips.txt)
- dynamic_f5_updater.py (最新版本，支援自動建立 DG 和 API 更新 iRule)
- dns_rpz_irule_template.tcl (您的 iRule 範本，包含 # START_DG_IP_MAP_BLOCK 和 # END_DG_IP_MAP_BLOCK 標記)
- rpz_fqdn_zone.txt (包含 FQDN Zone 列表)
- rpz_ip_zone.txt (包含 IP Zone 列表，如果沒有 IP Zone，此檔案可為空)
- f5_devices.txt (包含 F5 IP, 用戶名, 設備名 - **不含密碼**)
- (可選) known_landing_ips.txt (如果需要預先定義，否則腳本會使用預設值或自行創建)
- .gitignore (建議包含 *.log, f5_datagroups/, venv/, __pycache__/, *.pyc)

**設定所有權：**

```bash
# 回到您的 sudo 用戶下執行
sudo chown -R rpz_user:rpzusers /opt/rpz_project
```

### 步驟 6：設定檔案權限

```bash
# 腳本需要執行權限
sudo chmod +x /opt/rpz_project/rpz_converter.py
sudo chmod +x /opt/rpz_project/dynamic_f5_updater.py

# 設定檔權限 (允許 rpz_user 讀寫，rpzusers 群組可讀)
sudo chmod 664 /opt/rpz_project/*.txt
sudo chmod 664 /opt/rpz_project/*.tcl
sudo chmod 664 /opt/rpz_project/.gitignore

# 確保 rpz_user 對目錄有寫入權限 (用於產生輸出檔和日誌)
sudo chmod 775 /opt/rpz_project
```

### 步驟 7：設定環境變數 (將在 Systemd 服務中定義)

以下是需要在 Systemd 服務設定檔中為 rpz_user 定義的環境變數：

- **F5 設備密碼：**
  - F5_PASSWORD_F5_Device1 (將 F5_Device1 替換為 f5_devices.txt 中定義的設備名稱，- 需換成 _)
  - F5_PASSWORD_F5_Device2
  - ... (為 f5_devices.txt 中的每個設備定義一個)

- **SMTP 密碼：**
  - SMTP_APP_PASSWORD

### 步驟 8：設定 Systemd 服務

- **建立 rpz-converter.service:**
  ```bash
  sudo nano /etc/systemd/system/rpz-converter.service
  ```
  
  貼入以下內容 (請將 <Your Actual SMTP App Password> 替換為真實密碼，並確認腳本路徑正確)：
  ```ini
  [Unit]
  Description=RPZ to F5 Data Group Converter Service
  After=network.target
  
  [Service]
  User=rpz_user
  Group=rpzusers
  WorkingDirectory=/opt/rpz_project
  Environment="SMTP_APP_PASSWORD=<Your Actual SMTP App Password>"
  # 如果 TSIG Key 也希望從環境變數讀取，可在此處加入 (需修改 rpz_converter.py)
  # Environment="TSIG_KEY_STRING_ENV_VAR=<Your TSIG Key String>"
  ExecStart=/opt/rpz_project/venv/bin/python3 /opt/rpz_project/rpz_converter.py
  Restart=always
  RestartSec=10
  StandardOutput=journal
  StandardError=journal
  
  [Install]
  WantedBy=multi-user.target
  ```

- **建立 f5-updater.service:**
  ```bash
  sudo nano /etc/systemd/system/f5-updater.service
  ```
  
  貼入以下內容 (請將 <Password for F5_DeviceX> 替換為真實密碼，並確認腳本路徑正確)：
  ```ini
  [Unit]
  Description=F5 External Data Group and iRule Updater Service
  After=network.target rpz-converter.service
  
  [Service]
  User=rpz_user
  Group=rpzusers
  WorkingDirectory=/opt/rpz_project
  Environment="F5_PASSWORD_F5_Device1=<Password for F5_Device1>"
  Environment="F5_PASSWORD_F5_Device2=<Password for F5_Device2>"
  # ... 為 f5_devices.txt 中的每個設備添加一行，注意設備名稱中的 '-' 需換成 '_' ...
  ExecStart=/opt/rpz_project/venv/bin/python3 /opt/rpz_project/dynamic_f5_updater.py
  Restart=always
  RestartSec=10
  StandardOutput=journal
  StandardError=journal
  
  [Install]
  WantedBy=multi-user.target
  ```
  
  **注意：** f5-updater.service 中 Environment 變數名稱的設備名部分 (如 F5_Device1) 必須與 f5_devices.txt 中定義的設備名（將 - 替換為 _ 後）一致，以便腳本能正確匹配。

- **重載 Systemd 並啟動/啟用服務：**
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable --now rpz-converter.service
  sudo systemctl enable --now f5-updater.service
  ```
  
  **重要提示：** 在首次啟用 f5-updater.service 之前，強烈建議先在 F5 上手動建立好腳本預期要管理的外部 Data Group 物件（至少是那些基於 DEFAULT_KNOWN_LANDING_IPS 和 PHISHTW_LANDING_IP 會產生的）。雖然腳本現在有自動建立 Data Group 的功能，但首次運行時，確保 F5 上有對應的空物件可以減少潛在問題。

- **檢查服務狀態：**
  ```bash
  sudo systemctl status rpz-converter.service
  sudo systemctl status f5-updater.service
  ```

- **查看服務日誌：**
  ```bash
  sudo journalctl -u rpz-converter.service -f
  sudo journalctl -u f5-updater.service -f
  ```

### 步驟 9：設定防火牆 (以 UFW 為例)

```bash
# 允許 SSH (如果尚未允許)
sudo ufw allow ssh

# 允許 F5 設備訪問轉換器提供的 HTTP 服務 (預設端口 8080)
# 請將 10.x.x.x/yy 替換為您 F5 設備實際所在的來源 IP 或網段
sudo ufw allow from 10.0.0.0/8 to any port 8080 proto tcp comment 'Allow F5 to fetch Data Groups'
# 或者更精確地指定多個 F5 IP
# sudo ufw allow from <F5_IP_1> to any port 8080 proto tcp
# sudo ufw allow from <F5_IP_2> to any port 8080 proto tcp

# 啟用防火牆 (如果尚未啟用)
# sudo ufw enable
```

### 步驟 10：監控帳號設定

- **讀取腳本自身日誌：**
  ```bash
  # 以 rpz_monitor 身份測試
  sudo -iu rpz_monitor
  cd /opt/rpz_project
  tail -f rpz_converter.log
  tail -f f5_updater.log
  exit
  ```

- **讀取 Systemd 日誌：**
  ```bash
  sudo usermod -aG systemd-journal rpz_monitor
  # rpz_monitor 需要重新登入才能使群組變更生效
  ```

### 步驟 11：首次執行與驗證

1. **檢查服務狀態與日誌：** 確認服務運行正常，沒有明顯錯誤。

2. **檢查 rpz_converter.py：**
   - 確認 rpz_converter.log 中 dig axfr 是否成功 (使用或不使用 TSIG Key)。
   - 確認 f5_datagroups/ 目錄下是否已產生預期的 .txt 檔案。
   - 確認 known_landing_ips.txt 是否被正確初始化或更新。

3. **檢查 dynamic_f5_updater.py：**
   - 確認 f5_updater.log 中 SSH 連接 F5 是否成功。
   - 確認 Data Group 的 list, create (如果需要), modify source-path 指令是否成功執行。
   - 確認 iRule 更新的 API 呼叫是否成功。

4. **檢查 F5 設備：**
   - 登入 F5，檢查對應的外部 Data Group 的 "Last Update Time" 和 "Source Path" 是否已更新。
   - 檢查目標 iRule 的內容，確認 dg_ip_map 是否已按預期更新。

5. **測試 Landing IP 變更：**
   - **新增 IP：** 在 DNS 伺服器為 MONITORED_ZONE 加入新的 Landing IP。觀察 rpz_converter.py 是否偵測到、更新 known_landing_ips.txt、發送 Email。觀察 dynamic_f5_updater.py 是否自動建立新的 Data Group 並更新 iRule。
   - **移除 IP：** 從 DNS 伺服器移除一個 MONITORED_ZONE 的 Landing IP。觀察 rpz_converter.py 是否偵測到、更新 known_landing_ips.txt。觀察 dynamic_f5_updater.py 是否在 iRule 中移除了對應的條目。

## F5 初始 Data Group 物件建立 (建議)

雖然 dynamic_f5_updater.py 腳本現在支援自動建立不存在的外部 Data Group，但在首次部署或重大變更後，建議先在 F5 上手動建立好腳本預期會管理的主要 Data Group 物件。這樣可以確保 source-path 的首次設定更平穩。

**範例 (在 TMSH 中執行)：**

```bash
# 針對 rpztw zone 的每個已知 Landing IP (假設 IP 為 A.B.C.D)
create ltm data-group external /Common/rpztw_A_B_C_D type string source-path http://<HTTP_SERVER_IP_PORT>/rpztw_A_B_C_D.txt


# 針對 phishtw zone 的特定 Landing IP (假設為 E.F.G.H)
create ltm data-group external /Common/phishtw_E_F_G_H type string source-path http://<HTTP_SERVER_IP_PORT>/phishtw_E_F_G_H.txt

```

將 <HTTP_SERVER_IP_PORT> 替換為實際的轉換器伺服器 IP 和端口 (例如 10.8.38.223:8080)。


## 常見問題與解決方案

### 1. "dig: command not found" 錯誤

**問題**: rpz_converter.py 日誌中顯示找不到 dig 命令。

**解決方案**: 安裝 dnsutils 套件：
```bash
sudo apt install dnsutils
```

### 2. F5 SSH 連接失敗

**問題**: f5_updater.log 顯示無法連接到 F5 設備。

**解決方案**: 
- 確認 F5 IP 位址是否正確
- 確認 F5 SSH 服務是否啟用
- 確認用戶名和環境變數中的密碼是否正確
- 檢查網路連通性：`ping <F5設備IP>`

### 3. HTTP 服務端口被佔用

**問題**: 啟動 rpz_converter.py 時，日誌顯示 HTTP 端口 8080 已被佔用。

**解決方案**: 
- 找出佔用端口的程序：`sudo lsof -i :8080`
- 終止該程序或修改 rpz_converter.py 中的 HTTP_PORT 設定

### 4. 環境變數問題

**問題**: 腳本無法讀取環境變數中的密碼。

**解決方案**: 
- 確認 Systemd 服務檔中環境變數設定正確
- 確認變數名稱符合預期 (尤其是 F5_PASSWORD_ 開頭的變數)
- 重新載入 Systemd 並重啟服務

請依照此 SOP 進行安裝設定。如果在過程中遇到任何問題或錯誤訊息，請參考上述常見問題，或記錄詳細資訊以供進一步分析。
