when DNS_REQUEST {
    # 獲取查詢名稱
    set query_name [DNS::question name]
    
    # 先檢查白名單
    if { [class match $query_name ends_with white_Domains] } {
        # 白名單域名，允許正常處理
        #log local0. "Request for white domain from: [IP::client_addr] Question: [DNS::question name]"
    } else {
        # 檢查黑名單
        if { [class match $query_name ends_with blacklist_Domains] } {
            if { [DNS::question type] equals "A"} {
                DNS::answer insert "[DNS::question name]. 600 [DNS::question class] [DNS::question type] 34.102.218.71"
                DNS::return
                #log local0. "Block true and A record. Request for Blocklist Domain from: [IP::client_addr] Question: [DNS::question name]"
            } else {
                if {[DNS::question type] equals "AAAA" } {
                    DNS::answer insert "[DNS::question name]. 600 [DNS::question class] [DNS::question type] 2600:1901:0:9b4c::"
                    DNS::return
                    #log local0. "Block true and A record. Request for Blocklist Domain from: [IP::client_addr] Question: [DNS::question name]"
                }
            }
        } else {
            # 檢查 RPZ 資料組
            if {[DNS::question type] eq "A"} {
                # 定義要檢查的資料組列表
                set data_groups [list "rpz_rpztw" "rpz_phishingtw"]
                set matched 0
                
                # 依序檢查每個資料組
                foreach dg $data_groups {
                    if {[class match $query_name equals $dg]} {
                        # 從資料組中獲取 IP 地址
                        set custom_ip [class lookup $query_name $dg]
                        
                        # 創建 DNS 響應
                        DNS::answer clear
                        DNS::answer insert "[DNS::question name]. 30 [DNS::question class] [DNS::question type] $custom_ip"
                        
                        # 設置匹配標誌
                        set matched 1
                        
                        # 紀錄匹配的資料組 (可選)
                        # log local0.info "DNS 查詢 $query_name 匹配資料組 $dg，返回 IP: $custom_ip"
                        
                        # 跳出迴圈，不需要檢查其他資料組
                        break
                    }
                }
                
                # 如果有匹配，發送響應並結束處理
                if {$matched} {
                    DNS::return
                }
            }
        }
    }
}
