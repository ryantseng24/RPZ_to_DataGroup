# 安裝與設定 RPZ 轉換器及 F5 更新腳本 SOP

## 文件目的

本文件旨在提供在全新的 Ubuntu LTS 伺服器上，安裝、設定並執行 RPZ 轉換器 (rpz_converter.py) 及 F5 Data Group 更新腳本 (dynamic_f5_updater.py) 的標準作業程序。

## 前提條件

* 已安裝好一台 Ubuntu LTS 伺服器（建議 20.04 LTS 或更新版本）。
* 伺服器具有網路連線能力，可以：
   * 連接到指定的 DNS 伺服器 (執行 dig axfr)。
   * 連接到指定的 SMTP 伺服器 (發送 Email 通知)。
   * 連接到所有目標 F5 設備 (執行 SSH)。
   * 被所有目標 F5 設備訪問（用於 F5 下載 Data Group 檔案的 HTTP 服務）。
* 執行此 SOP 文件進行安裝與設定的人員，需要具有該 Ubuntu 伺服器的 sudo 權限 (用於建立用戶、安裝套件、設定服務等)。運行腳本的 rpz_user 帳號本身不應具有 sudo 權限。

## 目標架構

* 在伺服器上建立專用、低權限的用戶來執行腳本。
* 使用 Python 虛擬環境隔離依賴。
* 將密碼等敏感資訊儲存在環境變數中（透過 Systemd 服務設定）。
* 使用 Systemd 將兩個腳本設定為背景服務，確保持續運行和自動重啟。

## 詳細步驟

### 步驟 1：建立用戶帳號

為了安全起見，我們不直接使用 root 或您個人的管理帳號來執行腳本。建立兩個專用帳號：
* rpz_user: 用於實際執行兩個 Python 腳本。
* rpz_monitor: 用於查看日誌和狀態（唯讀）。

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
sudo chmod 775 /opt/rpz_project # A允許群組成員寫入 (方便管理)

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

在 rpz_user 身份下，並啟動虛擬環境後，安裝 dynamic_f5_updater.py 需要的 paramiko 函式庫。

```bash
# 確保您是以 rpz_user 身份並已啟動 venv
# (提示符號應該類似 (venv) rpz_user@hostname:/opt/rpz_project$)
pip install paramiko

# (可選) 退出虛擬環境 (如果不需要立即執行其他 pip 指令)
# deactivate
```

### 步驟 5：放置腳本與設定檔

將以下檔案放置到 /opt/rpz_project/ 目錄下：
* rpz_converter.py (修改後的 rpz_converter_env_password 腳本)
* dynamic_f5_updater.py (修改後的 update_f5_datagroups_dynamic 腳本)
* rpz_fqdn_zone.txt (包含 FQDN Zone 列表)
* rpz_ip_zone.txt (包含 IP Zone 列表)
* f5_devices.txt (包含 F5 IP, 用戶名, 設備名 - 不含密碼)
* (可選) known_landing_ips.txt (如果需要預先定義，否則腳本會使用預設值或自行創建)

設定所有權： 確保這些檔案都屬於 rpz_user。

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
# 或者更嚴格，只讓 rpz_user 可讀寫
# sudo chmod 600 /opt/rpz_project/*.txt
# sudo chmod 600 /opt/rpz_project/f5_devices.txt # f5_devices.txt 權限可設更嚴格

# 確保 rpz_user 對目錄有寫入權限 (用於產生輸出檔和日誌)
sudo chmod 775 /opt/rpz_project
```

### 步驟 7：設定環境變數 (透過 Systemd)

我們不在 ~/.bashrc 中設定環境變數，而是直接在 Systemd 服務設定檔中定義，這樣更安全且只對服務本身可見。此步驟只是先了解需要設定哪些變數，實際設定在下一步。

需要設定的變數 (由 rpz_user 執行的服務需要)：
* F5_PASSWORD_F5_Device1 (設備名中的 - 已換成 _)
* F5_PASSWORD_F5_Device2 (設備名中的 - 已換成 _)
* ... (根據 f5_devices.txt 列出所有 F5)
* SMTP_APP_PASSWORD

### 步驟 8：設定 Systemd 服務

建立兩個 Systemd unit file 來管理這兩個腳本。

* 建立 rpz-converter.service:

```bash
sudo nano /etc/systemd/system/rpz-converter.service
```

貼入以下內容 (請根據實際情況修改 WorkingDirectory, ExecStart 中的路徑, 以及 Environment 中的密碼):

```
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
StandardError=journal  # 將錯誤導向 systemd journal

[Install]
WantedBy=multi-user.target
```

注意： 請將 <Your Actual SMTP App Password> 替換為真實的密碼。

* 建立 f5-updater.service:

```bash
sudo nano /etc/systemd/system/f5-updater.service
```

貼入以下內容 (請根據實際情況修改 WorkingDirectory, ExecStart 中的路徑, 以及 Environment 中的密碼):

```
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
StandardError=journal  # 將錯誤導向 systemd journal

[Install]
WantedBy=multi-user.target
```

注意： 請將 <Password for F5_Device1> 等替換為對應 F5 設備的真實密碼。

* 重載 Systemd 並啟動服務：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rpz-converter.service
sudo systemctl enable --now f5-updater.service
```

* 檢查服務狀態：

```bash
sudo systemctl status rpz-converter.service
sudo systemctl status f5-updater.service
```

確認它們是 active (running) 狀態。

* 查看服務日誌：

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

注意： 出站規則 (Outgoing) 通常預設是允許的，所以腳本訪問 DNS, SMTP, F5 SSH 通常不需要額外設定出站規則，除非您有非常嚴格的預設拒絕策略。

### 步驟 10：監控帳號設定

設定 rpz_monitor 帳號的權限。

* 讀取日誌檔案：
  * 腳本產生的日誌檔案 (rpz_converter.log, f5_updater.log) 預設會寫在 /opt/rpz_project 目錄下，擁有者是 rpz_user。
  * 由於 rpz_monitor 和 rpz_user 都在 rpzusers 群組，且目錄和檔案權限設定為 664 或 775，rpz_monitor 應該可以直接讀取這些日誌。

```bash
# 以 rpz_monitor 身份測試
sudo -iu rpz_monitor
cd /opt/rpz_project
tail -f rpz_converter.log
tail -f f5_updater.log
exit
```

* 讀取 Systemd 日誌：
  * 預設情況下，非特權用戶可能無法直接讀取所有 systemd 日誌。
  * 可以考慮將 rpz_monitor 加入 systemd-journal 群組，使其可以讀取更多日誌。

```bash
sudo usermod -aG systemd-journal rpz_monitor
# rpz_monitor 需要重新登入才能使群組變更生效
```

### 步驟 11：首次執行與驗證

1. 檢查服務狀態： 使用 systemctl status 確認兩個服務都在運行。
2. 檢查 Systemd 日誌： 使用 journalctl -u <service_name> 查看是否有啟動錯誤或運行中的錯誤訊息。
3. 檢查腳本自身日誌： 查看 /opt/rpz_project/rpz_converter.log 和 /opt/rpz_project/f5_updater.log。
4. 檢查輸出目錄： 查看 /opt/rpz_project/f5_datagroups/ 目錄下是否成功產生了 .txt 檔案。
5. 檢查 F5 更新： 觀察 f5_updater.log 是否有成功連接 F5 並執行 tmsh modify 指令的記錄。登入 F5 檢查對應的外部 Data Group 的 Update Time 是否有更新。
6. 檢查 Email 通知： （如果啟用了 Email）觸發一個 Landing IP 變更（例如手動修改 known_landing_ips.txt 再等轉換器運行），看看是否收到 Email。

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
