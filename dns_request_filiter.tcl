when DNS_REQUEST {
    # 加回 set query_name 變數
    set query_name [DNS::question name]

    # --- 1. 白名單檢查 (同 v8) ---
    if { [class match $query_name ends_with white_Domains] } {
        return
    }

    # --- 2. 黑名單檢查 (同 v8) ---
    if { [class match $query_name ends_with blacklist_Domains] } {
        # 改回使用 equals
        if { [DNS::question type] equals "A"} {
            # 使用 [DNS::question type]
            DNS::answer insert "$query_name. 600 [DNS::question class] [DNS::question type] 34.102.218.71"
            DNS::return
        } elseif {[DNS::question type] equals "AAAA" } {
             # 使用 [DNS::question type]
            DNS::answer insert "$query_name. 600 [DNS::question class] [DNS::question type] 2600:1901:0:9b4c::"
            DNS::return
        }
        # 移除 else 區塊 (如果非 A/AAAA 命中黑名單，會繼續往下)
    }

    # --- 3. 檢查多個策略 Data Group (修改結構：先查名單再判斷類型) ---
    # 使用原始變數名 dg_ip_map
    # *** 移除內部註解 ***
    set dg_ip_map {
        "rpztw_34_102_218_71"   "34.102.218.71"
        "rpztw_182_173_0_181"  "182.173.0.181"
        "rpztw_112_121_114_76"  "112.121.114.76"
        "rpztw_210_64_24_25"    "210.64.24.25"
        "rpztw_210_69_155_3"    "210.69.155.3"
        "rpztw_35_206_236_238"  "35.206.236.238"
        "phishtw_182_173_0_170" "182.173.0.170"
    }

    # 遍歷映射表 (單一迴圈)
    # 使用原始變數名 dg, ip
    foreach {dg ip} $dg_ip_map {
        # 1. 先檢查 $query_name 是否匹配名為 dg 的 Data Group 內部的記錄
        if { [class match $query_name ends_with $dg] } {
            # 匹配成功!
            # 2. 再判斷查詢類型
            if { [DNS::question type] eq "A" } {
                # A 類型: 回應 IP
                DNS::answer clear
                # 使用 [DNS::question type]
                DNS::answer insert "$query_name. 30 [DNS::question class] [DNS::question type] $ip"
                DNS::return
            } else {
                # 非 A 類型: 回應 SOA
                DNS::answer clear
                # 使用 IN SOA
                DNS::answer insert "$query_name. 30 IN SOA ns.rpz.local. admin.rpz.local. 2023010101 3600 600 86400 30"
                DNS::return
            }
        }
    }

    # --- 4. 如果沒有任何匹配，則請求正常處理 ---
}